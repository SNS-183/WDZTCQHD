from flask import Flask, g, jsonify, request, session
from flask_cors import CORS
from functools import wraps
import json
import logging
import os
import secrets
import time

import pymysql
from werkzeug.security import check_password_hash, generate_password_hash

from analysis_compare import compare_task_snapshots

from app_logic import (
    build_extract_result,
    build_wordcloud_data,
    parse_extract_params,
    parse_extract_texts,
    parse_request_file_names,
    parse_wordcloud_params,
    parse_wordcloud_texts,
)
from database import (
    clear_task_history,
    confirm_task_topic,
    batch_update_tasks,
    create_analysis_task_record,
    create_user,
    create_task_share,
    copy_task,
    delete_task_by_id,
    fetch_task_detail,
    fetch_task_summary,
    fetch_task_comparison_snapshots,
    fetch_task_audit,
    fetch_admin_statistics,
    query_task_page,
    rename_task_topic,
    delete_task_topic,
    merge_task_topics,
    find_user_by_username,
    find_existing_document_names,
    find_task_by_request_id,
    get_task_retry_payload,
    fetch_shared_task,
    save_task_filter,
    list_task_filters,
    delete_task_filter,
    init_database,
    save_extract_result,
    save_wordcloud_result,
    update_analysis_task_status,
    update_analysis_task_progress,
    update_task_metadata,
    TASK_STATUS_ERROR,
    TASK_STATUS_DONE,
)


class JsonLogFormatter(logging.Formatter):
    """将服务日志输出为便于采集的单行 JSON。"""

    def format(self, record):
        payload = {
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


app = Flask(__name__)
secret_key = os.getenv("APP_SECRET_KEY") or secrets.token_hex(32)
app.config.update(
    SECRET_KEY=secret_key,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("SESSION_COOKIE_SECURE", "false").lower() == "true",
    MAX_CONTENT_LENGTH=int(os.getenv("MAX_REQUEST_BYTES", str(32 * 1024 * 1024))),
)

# 仅允许配置中的前端地址携带会话 Cookie，避免开放跨域读取用户数据。
cors_origins = [
    origin.strip()
    for origin in os.getenv(
        "CORS_ORIGINS",
        "http://127.0.0.1:5173,http://localhost:5173,http://127.0.0.1:5175,http://localhost:5175",
    ).split(",")
    if origin.strip()
]
CORS(app, origins=cors_origins, supports_credentials=True)

# 配置结构化日志，生产环境可直接交给日志采集器处理。
log_handler = logging.StreamHandler()
log_handler.setFormatter(JsonLogFormatter())
logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO),
    handlers=[log_handler],
)
logger = logging.getLogger(__name__)

@app.errorhandler(413)
def handle_request_too_large(_error):
    """用统一 JSON 格式返回请求体超限错误。"""
    return jsonify({"code": 413, "msg": "请求体过大，请减少文件数量或文件大小"}), 413


@app.route("/api/health", methods=["GET"])
def health_check():
    """供负载均衡与进程守护探测服务存活状态。"""
    return jsonify({"code": 200, "msg": "服务正常", "data": {"status": "ok"}}), 200


def login_required(view_func):
    """要求请求携带有效登录会话，并向当前请求注入用户上下文。"""

    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if request.method == "OPTIONS":
            return view_func(*args, **kwargs)
        user_id = session.get("user_id")
        if not user_id:
            return jsonify({"code": 401, "msg": "登录状态已失效，请重新登录"}), 401
        g.current_user_id = int(user_id)
        g.current_username = str(session.get("username", "") or "")
        return view_func(*args, **kwargs)

    return wrapped


def admin_required(view_func):
    """要求管理员会话，管理员接口仅提供只读聚合信息。"""

    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            return jsonify({"code": 401, "msg": "登录状态已失效，请重新登录"}), 401
        if not session.get("is_admin"):
            return jsonify({"code": 403, "msg": "仅管理员可访问"}), 403
        g.current_user_id = int(session["user_id"])
        g.current_username = str(session.get("username", "") or "")
        return view_func(*args, **kwargs)

    return wrapped


def _parse_auth_payload():
    """解析登录/注册请求体，只接收用户名和密码。"""
    request_data = request.get_json(silent=True)
    if not isinstance(request_data, dict):
        return None, None, ("请求体必须为 JSON 对象", 400)

    username = str(request_data.get("username", "") or "").strip()
    password = str(request_data.get("password", "") or "")
    if not username or not password:
        return None, None, ("请填写用户名与密码", 400)
    if len(username) > 32:
        return None, None, ("用户名长度不能超过 32 个字符", 400)
    if len(password) < 6:
        return None, None, ("密码长度不能少于 6 位", 400)
    return username, password, None


@app.route("/api/auth/register", methods=["POST", "OPTIONS"])
def register_user():
    """注册用户账号，并将密码哈希写入数据库。"""
    if request.method == "OPTIONS":
        return jsonify({"status": "ok"}), 200

    username, password, error = _parse_auth_payload()
    if error:
        msg, status_code = error
        return jsonify({"code": status_code, "msg": msg}), status_code

    try:
        user = create_user(username, generate_password_hash(password))
        return jsonify({
            "code": 200,
            "msg": "注册成功",
            "data": {
                "id": user["user_id"],
                "username": user["username"],
            },
        }), 200
    except pymysql.err.IntegrityError:
        return jsonify({"code": 409, "msg": "用户名已存在，请更换用户名"}), 409
    except Exception as exc:
        logger.error(f"注册用户失败: {str(exc)}")
        return jsonify({"code": 500, "msg": f"服务器错误：注册失败"}), 500


@app.route("/api/auth/login", methods=["POST", "OPTIONS"])
def login_user():
    """校验数据库中的用户名与密码，未注册用户无法登录。"""
    if request.method == "OPTIONS":
        return jsonify({"status": "ok"}), 200

    username, password, error = _parse_auth_payload()
    if error:
        msg, status_code = error
        return jsonify({"code": status_code, "msg": msg}), status_code

    try:
        user = find_user_by_username(username)
        if user is None or not check_password_hash(user["password_hash"], password):
            return jsonify({"code": 401, "msg": "用户名或密码错误"}), 401

        # 登录成功后只在服务端签名会话中保存身份，后续不再信任客户端自报用户名。
        session.clear()
        session["user_id"] = int(user["user_id"])
        session["username"] = str(user["username"])
        session["is_admin"] = bool(user.get("is_admin", False))
        return jsonify({
            "code": 200,
            "msg": "登录成功",
            "data": {
                "id": int(user["user_id"]),
                "username": user["username"],
                "is_admin": bool(user.get("is_admin", False)),
            },
        }), 200
    except Exception as exc:
        logger.error(f"登录用户失败: {str(exc)}")
        return jsonify({"code": 500, "msg": "服务器错误：登录失败"}), 500


@app.route("/api/auth/me", methods=["GET"])
@login_required
def get_current_user():
    """返回服务端会话中的当前用户，不读取客户端本地身份标记。"""
    return jsonify({
        "code": 200,
        "msg": "获取当前用户成功",
        "data": {
            "id": g.current_user_id,
            "username": g.current_username,
            "is_admin": bool(session.get("is_admin", False)),
        },
    }), 200


@app.route("/api/admin/statistics", methods=["GET"])
@admin_required
def get_admin_statistics():
    """返回管理员只读聚合统计。"""
    try:
        return jsonify({"code": 200, "msg": "获取系统统计成功", "data": fetch_admin_statistics()}), 200
    except Exception as exc:
        logger.error(f"获取管理员统计失败: {str(exc)}")
        return jsonify({"code": 500, "msg": "服务器错误：获取系统统计失败"}), 500


@app.route("/api/auth/logout", methods=["POST", "OPTIONS"])
def logout_user():
    """清除服务端签名会话。"""
    if request.method == "OPTIONS":
        return jsonify({"status": "ok"}), 200
    session.clear()
    return jsonify({"code": 200, "msg": "退出登录成功", "data": {}}), 200


@app.route("/extract", methods=["POST", "OPTIONS"])
@login_required
def extract_interests():
    """统一处理主题提取与词云请求。"""
    if request.method == "OPTIONS":
        return jsonify({"status": "ok"}), 200

    pending_task_id = None
    try:
        request_data = request.get_json(silent=True)
        logger.info(
            f"收到请求，数据keys: {list(request_data.keys()) if isinstance(request_data, dict) else '无/非JSON'}"
        )

        if not isinstance(request_data, dict):
            return jsonify({"code": 400, "msg": "请求体必须为 JSON 对象"}), 400

        # 用户身份只来自签名会话，丢弃旧客户端可能继续提交的 username 字段。
        request_data.pop("username", None)

        wordcloud_only = request_data.get("wordcloud_only", False)
        if not isinstance(wordcloud_only, bool):
            return jsonify({"code": 400, "msg": "参数错误：wordcloud_only 必须为布尔值"}), 400

        # 词云模式走独立服务层逻辑，但仍复用同一个接口。
        if wordcloud_only:
            texts, err_msg = parse_wordcloud_texts(request_data)
            if err_msg:
                return jsonify({"code": 400, "msg": err_msg}), 400

            params, err_msg = parse_wordcloud_params(request_data)
            if err_msg:
                return jsonify({"code": 400, "msg": err_msg}), 400

            start_ts = time.time()
            wordcloud_data = build_wordcloud_data(texts, params)
            wordcloud_data["stats"]["processing_time_ms"] = round((time.time() - start_ts) * 1000.0, 2)
            response_data = {"code": 200, "msg": "获取词云成功", "data": wordcloud_data}
            save_wordcloud_result(request_data, response_data)
            return jsonify(response_data), 200

        texts, err_msg = parse_extract_texts(request_data)
        if err_msg:
            return jsonify({"code": 400, "msg": err_msg}), 400

        params, err_msg = parse_extract_params(request_data)
        if err_msg:
            return jsonify({"code": 400, "msg": err_msg}), 400

        record_recent = request_data.get("record_recent", True)
        if not isinstance(record_recent, bool):
            return jsonify({"code": 400, "msg": "参数错误：record_recent 必须为布尔值"}), 400

        request_id = str(request_data.get("request_id", "") or "").strip()
        if request_id and (len(request_id) > 64 or not all(ch.isalnum() or ch in "-_.:" for ch in request_id)):
            return jsonify({"code": 400, "msg": "参数错误：request_id 格式无效"}), 400
        if record_recent and request_id:
            existing_task = find_task_by_request_id(g.current_user_id, request_id)
            if existing_task:
                if existing_task["task_status"] == TASK_STATUS_DONE and isinstance(
                    existing_task.get("response_payload"), dict
                ):
                    # 已完成请求直接返回快照，并补齐历史快照中可能缺失的批次 ID。
                    replay_response = dict(existing_task["response_payload"])
                    replay_data = dict(replay_response.get("data") or {})
                    replay_data["task_id"] = existing_task["task_id"]
                    replay_data["batch_id"] = existing_task["task_id"]
                    replay_response["data"] = replay_data
                    return jsonify(replay_response), 200
                return jsonify({
                    "code": 409,
                    "msg": "相同请求已提交，当前状态：" + existing_task["task_status"],
                    "data": {"task_id": existing_task["task_id"]},
                }), 409

        file_names, err_msg = parse_request_file_names(request_data, len(texts))
        if err_msg:
            return jsonify({"code": 400, "msg": err_msg}), 400
        unique_check_names = []
        raw_unique_check_names = request_data.get("unique_name_file_names", [])
        if isinstance(raw_unique_check_names, list):
            unique_check_names = [
                str(name).strip()
                for name in raw_unique_check_names
                if str(name).strip()
            ]
        elif request_data.get("unique_name_check") is True:
            unique_check_names = [
                str(name).strip()
                for name in file_names
                if str(name).strip()
            ]
        if unique_check_names:
            duplicate_in_request = sorted({
                name for name in unique_check_names if unique_check_names.count(name) > 1
            })
            if duplicate_in_request:
                return jsonify({
                    "code": 400,
                    "msg": "文本录入文档名重复：" + "、".join(duplicate_in_request),
                }), 400
            existing_names = find_existing_document_names(
                unique_check_names,
                g.current_user_id,
            )
            if existing_names:
                return jsonify({
                    "code": 400,
                    "msg": "当前账号下已存在同名手工文档，请更换标题后再抽取：" + "、".join(existing_names),
                }), 400

        extract_user_id = g.current_user_id

        if record_recent:
            # 先写入“分析中”批次，后续成功/失败都会更新该记录。
            pending_task_id = create_analysis_task_record(request_data, user_id=extract_user_id)
            update_analysis_task_progress(pending_task_id, extract_user_id, 20)

        http_start_ts = time.time()
        extract_result = build_extract_result(texts, file_names, params)
        if pending_task_id:
            update_analysis_task_progress(pending_task_id, extract_user_id, 85)
        statistics = dict(extract_result["statistics"])
        statistics["processing_time_ms"] = (time.time() - http_start_ts) * 1000.0

        total_theme_count = int(statistics["theme_count"])
        total_doc_theme_count = int(statistics["doc_theme_count"])
        response_data = {
            "code": 200,
            "msg": (
                f"成功处理 {len(extract_result['files'])} 个文件，"
                f"提取到 {total_theme_count} 个全局主题（{total_doc_theme_count} 条文档主题关联）"
            ),
            "data": {
                "themes": extract_result["doc_themes"],
                "doc_themes": extract_result["doc_themes"],
                "file_count": len(extract_result["files"]),
                "theme_count": total_theme_count,
                "doc_theme_count": total_doc_theme_count,
                "files": extract_result["files"],
                "statistics": statistics,
            },
        }

        if params["return_topics"]:
            response_data["data"]["topics"] = extract_result["topics"]
        if params["return_matrix"]:
            response_data["data"]["matrix"] = extract_result["matrix"]
            response_data["data"]["relation"] = extract_result["relation"]
            response_data["data"]["heatmap"] = extract_result["heatmap"]
        if params["debug"]:
            response_data["data"]["debug"] = {
                "request_params": params,
                "modeled_topic_k": extract_result["debug"]["modeled_topic_k"],
                "unit_count": extract_result["debug"]["unit_count"],
                "record_recent": record_recent,
            }

        if record_recent:
            saved_task = save_extract_result(
                extract_result,
                statistics,
                request_payload=request_data,
                response_payload=response_data,
                user_id=extract_user_id,
                existing_task_id=pending_task_id,
            )
            if isinstance(saved_task, dict):
                # 对外只返回分析批次 ID，文档主键保留在 saved_task 内部明细中。
                response_data["data"]["saved_task"] = saved_task
                batch_id = saved_task.get("batch_id") or saved_task.get("task_id")
                response_data["data"]["task_id"] = batch_id
                response_data["data"]["batch_id"] = batch_id

        logger.info(f"请求处理完成，总共提取到 {total_theme_count} 个主题")
        return jsonify(response_data), 200

    except Exception as exc:
        logger.error(f"服务器错误: {str(exc)}")
        error_response = {"code": 500, "msg": f"服务器错误：{str(exc)}"}
        if pending_task_id:
            try:
                update_analysis_task_status(pending_task_id, TASK_STATUS_ERROR, error_response)
            except Exception as status_exc:
                logger.error(f"更新失败任务状态失败: {str(status_exc)}")
        return jsonify(error_response), 500


@app.route("/task", methods=["GET"])
@login_required
def get_recent_docs():
    """分页返回当前用户任务，支持关键词、状态、时间范围和排序。"""
    try:
        try:
            page = int(request.args.get("page", 1))
            page_size = int(request.args.get("page_size", 20))
            days = int(request.args.get("days", 0))
            focus_task_id = int(request.args.get("focus_task_id", 0)) or None
        except (TypeError, ValueError):
            return jsonify({"code": 400, "msg": "分页、时间范围和定位任务参数必须为整数"}), 400
        if page < 1 or not 1 <= page_size <= 100:
            return jsonify({"code": 400, "msg": "page 必须大于 0，page_size 需在 1~100 之间"}), 400
        if days not in {0, 7, 30, 90}:
            return jsonify({"code": 400, "msg": "days 仅支持 0、7、30、90"}), 400

        keyword = str(request.args.get("keyword", "") or "").strip()[:100]
        status = str(request.args.get("status", "all") or "all").strip().lower()
        sort_order = str(request.args.get("sort", "newest") or "newest").strip().lower()
        archived = str(request.args.get("archived", "active") or "active").strip().lower()
        if status not in {"all", "done", "running", "error"}:
            return jsonify({"code": 400, "msg": "status 参数无效"}), 400
        if sort_order not in {"newest", "oldest"}:
            return jsonify({"code": 400, "msg": "sort 仅支持 newest 或 oldest"}), 400
        if archived not in {"active", "archived", "all"}:
            return jsonify({"code": 400, "msg": "archived 仅支持 active、archived 或 all"}), 400

        page_result = query_task_page(
            g.current_user_id,
            page=page,
            page_size=page_size,
            keyword=keyword,
            status=status,
            days=days,
            sort_order=sort_order,
            focus_task_id=focus_task_id,
            archived=archived,
        )
        return jsonify({
            "code": 200,
            "msg": "获取成功",
            "data": page_result["items"],
            "pagination": page_result["pagination"],
            "focus_page": page_result.get("focus_page"),
            "summary": fetch_task_summary(g.current_user_id),
        }), 200
    except Exception as exc:
        logger.error(f"获取 recent-docs 错误: {str(exc)}")
        return jsonify({"code": 500, "msg": f"服务器错误：{str(exc)}"}), 500


@app.route("/task/<int:task_id>", methods=["GET"])
@login_required
def get_task_detail(task_id: int):
    """返回当前用户指定任务所在批次的完整分析结果。"""
    try:
        detail = fetch_task_detail(task_id, g.current_user_id)
        if not detail:
            return jsonify({"code": 404, "msg": "任务不存在"}), 404
        return jsonify(detail), 200
    except Exception as exc:
        logger.error(f"获取任务详情错误: {str(exc)}")
        return jsonify({"code": 500, "msg": f"服务器错误：{str(exc)}"}), 500


@app.route("/task/compare", methods=["POST"])
@login_required
def compare_tasks():
    """对比当前用户选择的 2~5 个分析任务。"""
    request_data = request.get_json(silent=True)
    raw_task_ids = request_data.get("task_ids") if isinstance(request_data, dict) else None
    if not isinstance(raw_task_ids, list) or not 2 <= len(raw_task_ids) <= 5:
        return jsonify({"code": 400, "msg": "请选择 2~5 个任务进行对比"}), 400
    try:
        task_ids = list(dict.fromkeys(int(task_id) for task_id in raw_task_ids))
    except (TypeError, ValueError):
        return jsonify({"code": 400, "msg": "任务 ID 必须为整数"}), 400
    if len(task_ids) < 2 or any(task_id <= 0 for task_id in task_ids):
        return jsonify({"code": 400, "msg": "请选择至少两个不同的有效任务"}), 400
    try:
        snapshots = fetch_task_comparison_snapshots(g.current_user_id, task_ids)
        if len(snapshots) != len(task_ids):
            return jsonify({"code": 404, "msg": "部分任务不存在或无权限访问"}), 404
        return jsonify({"code": 200, "msg": "任务对比成功", "data": compare_task_snapshots(snapshots)}), 200
    except Exception as exc:
        logger.error(f"任务对比失败: {str(exc)}")
        return jsonify({"code": 500, "msg": "服务器错误：任务对比失败"}), 500


@app.route("/task/filters", methods=["GET", "POST"])
@login_required
def task_filters():
    """列出或保存当前用户的任务筛选条件。"""
    if request.method == "GET":
        return jsonify({"code": 200, "msg": "获取筛选条件成功", "data": list_task_filters(g.current_user_id)}), 200
    request_data = request.get_json(silent=True)
    if not isinstance(request_data, dict):
        return jsonify({"code": 400, "msg": "请求体必须为 JSON 对象"}), 400
    name = str(request_data.get("name", "") or "").strip()
    filters = request_data.get("filters")
    if not name or len(name) > 100 or not isinstance(filters, dict):
        return jsonify({"code": 400, "msg": "筛选名称或筛选内容无效"}), 400
    allowed_keys = {"keyword", "status", "timeRange", "sort", "archived"}
    clean_filters = {key: filters[key] for key in allowed_keys if key in filters}
    return jsonify({
        "code": 200,
        "msg": "保存筛选条件成功",
        "data": save_task_filter(g.current_user_id, name, clean_filters),
    }), 200


@app.route("/task/filters/<int:filter_id>", methods=["DELETE"])
@login_required
def remove_task_filter(filter_id: int):
    """删除当前用户保存的筛选条件。"""
    if not delete_task_filter(filter_id, g.current_user_id):
        return jsonify({"code": 404, "msg": "筛选条件不存在"}), 404
    return jsonify({"code": 200, "msg": "筛选条件删除成功"}), 200


@app.route("/task/batch", methods=["POST"])
@login_required
def batch_tasks():
    """批量归档、恢复、打标签或删除当前用户任务。"""
    request_data = request.get_json(silent=True)
    if not isinstance(request_data, dict):
        return jsonify({"code": 400, "msg": "请求体必须为 JSON 对象"}), 400
    action = str(request_data.get("action", "") or "").strip().lower()
    raw_task_ids = request_data.get("task_ids")
    if action not in {"archive", "restore", "tag", "delete"}:
        return jsonify({"code": 400, "msg": "不支持的批量操作"}), 400
    if not isinstance(raw_task_ids, list) or not raw_task_ids or len(raw_task_ids) > 100:
        return jsonify({"code": 400, "msg": "task_ids 需包含 1~100 个任务 ID"}), 400
    try:
        task_ids = list(dict.fromkeys(int(task_id) for task_id in raw_task_ids))
    except (TypeError, ValueError):
        return jsonify({"code": 400, "msg": "任务 ID 必须为整数"}), 400
    if any(task_id <= 0 for task_id in task_ids):
        return jsonify({"code": 400, "msg": "任务 ID 必须大于 0"}), 400

    tags = None
    if action == "tag":
        raw_tags = request_data.get("tags")
        if not isinstance(raw_tags, list):
            return jsonify({"code": 400, "msg": "批量打标签时 tags 必须为数组"}), 400
        tags = list(dict.fromkeys(str(tag or "").strip() for tag in raw_tags if str(tag or "").strip()))
        if not tags or len(tags) > 10 or any(len(tag) > 20 for tag in tags):
            return jsonify({"code": 400, "msg": "标签数量或长度不符合要求"}), 400

    try:
        result = batch_update_tasks(
            g.current_user_id,
            task_ids,
            action,
            tags=tags,
        )
        return jsonify({"code": 200, "msg": "批量操作成功", "data": result}), 200
    except Exception as exc:
        logger.error(f"批量操作任务失败: {str(exc)}")
        return jsonify({"code": 500, "msg": "服务器错误：批量操作失败"}), 500


@app.route("/task/<int:task_id>", methods=["DELETE"])
@login_required
def delete_task(task_id: int):
    """删除当前用户的单条任务及其关联主题数据。"""
    try:
        deleted = delete_task_by_id(task_id, g.current_user_id)
        if not deleted:
            return jsonify({"code": 404, "msg": "任务不存在或无权限删除"}), 404
        return jsonify({"code": 200, "msg": "删除任务成功", "data": {"task_id": int(task_id)}}), 200
    except Exception as exc:
        logger.error(f"删除任务错误: {str(exc)}")
        return jsonify({"code": 500, "msg": f"服务器错误：{str(exc)}"}), 500


@app.route("/task/<int:task_id>", methods=["PATCH"])
@login_required
def patch_task(task_id: int):
    """更新任务名称、标签和归档状态。"""
    request_data = request.get_json(silent=True)
    if not isinstance(request_data, dict):
        return jsonify({"code": 400, "msg": "请求体必须为 JSON 对象"}), 400

    name = None
    if "name" in request_data:
        name = str(request_data.get("name", "") or "").strip()
        if not name or len(name) > 100:
            return jsonify({"code": 400, "msg": "任务名称长度需在 1~100 个字符之间"}), 400

    tags = None
    if "tags" in request_data:
        raw_tags = request_data.get("tags")
        if not isinstance(raw_tags, list):
            return jsonify({"code": 400, "msg": "tags 必须为数组"}), 400
        tags = []
        for item in raw_tags:
            tag = str(item or "").strip()
            if tag and tag not in tags:
                tags.append(tag)
        if len(tags) > 10 or any(len(tag) > 20 for tag in tags):
            return jsonify({"code": 400, "msg": "最多设置 10 个标签，每个标签不超过 20 字"}), 400

    archived = request_data.get("archived") if "archived" in request_data else None
    if archived is not None and not isinstance(archived, bool):
        return jsonify({"code": 400, "msg": "archived 必须为布尔值"}), 400
    if name is None and tags is None and archived is None:
        return jsonify({"code": 400, "msg": "至少提供一个可更新字段"}), 400

    try:
        result = update_task_metadata(
            task_id,
            g.current_user_id,
            name=name,
            tags=tags,
            archived=archived,
        )
        if not result:
            return jsonify({"code": 404, "msg": "任务不存在或无权限修改"}), 404
        return jsonify({"code": 200, "msg": "任务信息更新成功", "data": result}), 200
    except Exception as exc:
        logger.error(f"更新任务信息失败: {str(exc)}")
        return jsonify({"code": 500, "msg": "服务器错误：任务信息更新失败"}), 500


@app.route("/task/<int:task_id>/copy", methods=["POST"])
@login_required
def copy_owned_task(task_id: int):
    """复制当前用户已保存的完整分析批次。"""
    try:
        result = copy_task(task_id, g.current_user_id)
        if not result:
            return jsonify({"code": 404, "msg": "任务不存在或无权限复制"}), 404
        return jsonify({"code": 200, "msg": "任务复制成功", "data": result}), 201
    except Exception as exc:
        logger.error(f"复制任务失败: {str(exc)}")
        return jsonify({"code": 500, "msg": "服务器错误：任务复制失败"}), 500


@app.route("/task/<int:task_id>/share", methods=["POST"])
@login_required
def share_task(task_id: int):
    """创建任务只读分享令牌。"""
    request_data = request.get_json(silent=True) or {}
    try:
        expires_days = int(request_data.get("expires_days", 7))
    except (TypeError, ValueError):
        return jsonify({"code": 400, "msg": "expires_days 必须为整数"}), 400
    if not 1 <= expires_days <= 30:
        return jsonify({"code": 400, "msg": "分享有效期需在 1~30 天之间"}), 400
    try:
        result = create_task_share(task_id, g.current_user_id, expires_days)
        if not result:
            return jsonify({"code": 404, "msg": "任务不存在或无权限分享"}), 404
        return jsonify({"code": 200, "msg": "只读分享已创建", "data": result}), 201
    except Exception as exc:
        logger.error(f"创建任务分享失败: {str(exc)}")
        return jsonify({"code": 500, "msg": "服务器错误：创建分享失败"}), 500


@app.route("/task/<int:task_id>/retry", methods=["POST"])
@login_required
def retry_failed_task(task_id: int):
    """为失败任务准备重新提交所需的原始请求。"""
    try:
        retry_payload = get_task_retry_payload(task_id, g.current_user_id)
        if not retry_payload:
            return jsonify({"code": 404, "msg": "失败任务不存在、无权限或缺少原始请求"}), 404
        return jsonify({
            "code": 200,
            "msg": "已准备重跑请求",
            "data": {"source_task_id": int(task_id), "request": retry_payload},
        }), 200
    except Exception as exc:
        logger.error(f"准备失败任务重跑失败: {str(exc)}")
        return jsonify({"code": 500, "msg": "服务器错误：准备重跑失败"}), 500


@app.route("/task/<int:task_id>/audit", methods=["GET"])
@login_required
def get_task_audit(task_id: int):
    """返回当前用户任务最近 100 条操作记录。"""
    try:
        rows = fetch_task_audit(task_id, g.current_user_id)
        if rows is None:
            return jsonify({"code": 404, "msg": "任务不存在或无权限查看"}), 404
        return jsonify({"code": 200, "msg": "获取审计记录成功", "data": rows}), 200
    except Exception as exc:
        logger.error(f"获取任务审计失败: {str(exc)}")
        return jsonify({"code": 500, "msg": "服务器错误：获取审计记录失败"}), 500


@app.route("/task/<int:task_id>/topics/<topic_identifier>", methods=["PATCH"])
@login_required
def rename_topic(task_id: int, topic_identifier: str):
    """重命名当前用户任务中的主题。"""
    request_data = request.get_json(silent=True)
    if not isinstance(request_data, dict):
        return jsonify({"code": 400, "msg": "请求体必须为 JSON 对象"}), 400
    new_name = str(request_data.get("name", "") or "").strip()
    if not new_name or len(new_name) > 100:
        return jsonify({"code": 400, "msg": "主题名称长度需在 1~100 个字符之间"}), 400
    try:
        result = rename_task_topic(task_id, topic_identifier, g.current_user_id, new_name)
        if not result:
            return jsonify({"code": 404, "msg": "主题不存在或无权限修改"}), 404
        return jsonify({"code": 200, "msg": "主题重命名成功", "data": result}), 200
    except Exception as exc:
        logger.error(f"重命名主题失败: {str(exc)}")
        return jsonify({"code": 500, "msg": "服务器错误：主题重命名失败"}), 500


@app.route(
    "/task/<int:task_id>/topics/<topic_identifier>/confirmation",
    methods=["PATCH"],
)
@login_required
def confirm_topic(task_id: int, topic_identifier: str):
    """更新主题的人工确认状态。"""
    request_data = request.get_json(silent=True)
    if not isinstance(request_data, dict) or not isinstance(request_data.get("confirmed"), bool):
        return jsonify({"code": 400, "msg": "confirmed 必须为布尔值"}), 400
    try:
        result = confirm_task_topic(
            task_id,
            topic_identifier,
            g.current_user_id,
            request_data["confirmed"],
        )
        if not result:
            return jsonify({"code": 404, "msg": "主题不存在或无权限修改"}), 404
        return jsonify({"code": 200, "msg": "主题确认状态更新成功", "data": result}), 200
    except Exception as exc:
        logger.error(f"更新主题确认状态失败: {str(exc)}")
        return jsonify({"code": 500, "msg": "服务器错误：确认状态更新失败"}), 500


@app.route("/task/<int:task_id>/topics/<topic_identifier>", methods=["DELETE"])
@login_required
def delete_topic(task_id: int, topic_identifier: str):
    """删除当前用户任务中的主题。"""
    try:
        deleted = delete_task_topic(task_id, topic_identifier, g.current_user_id)
        if not deleted:
            return jsonify({"code": 404, "msg": "主题不存在或无权限删除"}), 404
        return jsonify({"code": 200, "msg": "主题删除成功", "data": {"id": topic_identifier}}), 200
    except Exception as exc:
        logger.error(f"删除主题失败: {str(exc)}")
        return jsonify({"code": 500, "msg": "服务器错误：主题删除失败"}), 500


@app.route("/task/<int:task_id>/topics/merge", methods=["POST"])
@login_required
def merge_topics(task_id: int):
    """合并当前用户任务中同一文档内的多个主题。"""
    request_data = request.get_json(silent=True)
    if not isinstance(request_data, dict):
        return jsonify({"code": 400, "msg": "请求体必须为 JSON 对象"}), 400
    topic_ids = request_data.get("topic_ids")
    merged_name = str(request_data.get("name", "") or "").strip()
    if not isinstance(topic_ids, list) or len(set(map(str, topic_ids))) < 2:
        return jsonify({"code": 400, "msg": "至少选择两个不同主题进行合并"}), 400
    if not merged_name or len(merged_name) > 100:
        return jsonify({"code": 400, "msg": "合并主题名称长度需在 1~100 个字符之间"}), 400
    try:
        result = merge_task_topics(
            task_id,
            [str(item) for item in topic_ids],
            g.current_user_id,
            merged_name,
        )
        if not result:
            return jsonify({"code": 404, "msg": "主题不存在或无权限合并"}), 404
        return jsonify({"code": 200, "msg": "主题合并成功", "data": result}), 200
    except ValueError as exc:
        return jsonify({"code": 400, "msg": str(exc)}), 400
    except Exception as exc:
        logger.error(f"合并主题失败: {str(exc)}")
        return jsonify({"code": 500, "msg": "服务器错误：主题合并失败"}), 500


@app.route("/task", methods=["DELETE"])
@login_required
def clear_tasks():
    """只清空当前登录用户的任务列表数据。"""
    try:
        result = clear_task_history(g.current_user_id)
        return jsonify({"code": 200, "msg": "清空当前用户任务成功", "data": result}), 200
    except Exception as exc:
        logger.error(f"清空任务错误: {str(exc)}")
        return jsonify({"code": 500, "msg": f"服务器错误：{str(exc)}"}), 500


@app.route("/share/<share_token>", methods=["GET"])
def get_shared_task(share_token: str):
    """无需登录读取有效令牌对应的脱敏只读任务。"""
    try:
        detail = fetch_shared_task(share_token)
        if not detail:
            return jsonify({"code": 404, "msg": "分享不存在或已过期"}), 404
        detail["msg"] = "获取只读分享成功"
        return jsonify(detail), 200
    except Exception as exc:
        logger.error(f"读取任务分享失败: {str(exc)}")
        return jsonify({"code": 500, "msg": "服务器错误：读取分享失败"}), 500


if __name__ == "__main__":
    try:
        init_database()
        logger.info("MySQL 数据库初始化完成")
    except Exception as exc:
        logger.error(f"MySQL 数据库初始化失败: {str(exc)}")
    app.run(
        host=os.getenv("APP_HOST", "127.0.0.1"),
        port=int(os.getenv("APP_PORT", "5000")),
        debug=os.getenv("FLASK_DEBUG", "false").lower() == "true",
    )
