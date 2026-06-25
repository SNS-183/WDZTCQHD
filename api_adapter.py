from flask import Flask, jsonify, request
from flask_cors import CORS
import logging
import time

import pymysql
from werkzeug.security import check_password_hash, generate_password_hash

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
    create_user,
    delete_task_by_id,
    fetch_task_detail,
    fetch_recent_files,
    find_user_by_username,
    find_user_id_by_username,
    find_existing_document_names,
    init_database,
    save_extract_result,
    save_wordcloud_result,
)


app = Flask(__name__)
CORS(app)

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MAX_RECENT = 20


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

        return jsonify({
            "code": 200,
            "msg": "登录成功",
            "data": {
                "id": int(user["user_id"]),
                "username": user["username"],
            },
        }), 200
    except Exception as exc:
        logger.error(f"登录用户失败: {str(exc)}")
        return jsonify({"code": 500, "msg": "服务器错误：登录失败"}), 500


@app.route("/extract", methods=["POST", "OPTIONS"])
def extract_interests():
    """统一处理主题提取与词云请求。"""
    if request.method == "OPTIONS":
        return jsonify({"status": "ok"}), 200

    try:
        request_data = request.get_json(silent=True)
        logger.info(
            f"收到请求，数据keys: {list(request_data.keys()) if isinstance(request_data, dict) else '无/非JSON'}"
        )

        if not isinstance(request_data, dict):
            return jsonify({"code": 400, "msg": "请求体必须为 JSON 对象"}), 400

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
            existing_names = find_existing_document_names(unique_check_names)
            if existing_names:
                return jsonify({
                    "code": 400,
                    "msg": "文本录入文档名已存在，请更换标题后再抽取：" + "、".join(existing_names),
                }), 400

        http_start_ts = time.time()
        extract_result = build_extract_result(texts, file_names, params)
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

        extract_user_id = None
        extract_username = str(request_data.get("username", "") or "").strip()
        if extract_username:
            extract_user_id = find_user_id_by_username(extract_username)

        if record_recent:
            save_extract_result(
                extract_result,
                statistics,
                request_payload=request_data,
                response_payload=response_data,
                user_id=extract_user_id,
            )

        logger.info(f"请求处理完成，总共提取到 {total_theme_count} 个主题")
        return jsonify(response_data), 200

    except Exception as exc:
        logger.error(f"服务器错误: {str(exc)}")
        return jsonify({"code": 500, "msg": f"服务器错误：{str(exc)}"}), 500


@app.route("/task", methods=["GET"])
def get_recent_docs():
    """返回最近上传/处理的文档列表。支持 ?username=xxx 过滤当前用户的任务。"""
    try:
        username = str(request.args.get("username", "") or "").strip() or None
        return jsonify({"code": 200, "msg": "获取成功", "data": fetch_recent_files(username, MAX_RECENT)}), 200
    except Exception as exc:
        logger.error(f"获取 recent-docs 错误: {str(exc)}")
        return jsonify({"code": 500, "msg": f"服务器错误：{str(exc)}"}), 500


@app.route("/task/<int:task_id>", methods=["GET"])
def get_task_detail(task_id: int):
    """返回指定任务所在批次的完整分析结果。"""
    try:
        detail = fetch_task_detail(task_id)
        if not detail:
            return jsonify({"code": 404, "msg": "任务不存在"}), 404
        return jsonify(detail), 200
    except Exception as exc:
        logger.error(f"获取任务详情错误: {str(exc)}")
        return jsonify({"code": 500, "msg": f"服务器错误：{str(exc)}"}), 500


@app.route("/task/<int:task_id>", methods=["DELETE"])
def delete_task(task_id: int):
    """删除单条任务及其关联主题数据。"""
    try:
        request_data = request.get_json(silent=True)
        username = str((request_data or {}).get("username", "") or "").strip() or None

        deleted = delete_task_by_id(task_id, username)
        if not deleted:
            return jsonify({"code": 404, "msg": "任务不存在或无权限删除"}), 404
        return jsonify({"code": 200, "msg": "删除任务成功", "data": {"task_id": int(task_id)}}), 200
    except Exception as exc:
        logger.error(f"删除任务错误: {str(exc)}")
        return jsonify({"code": 500, "msg": f"服务器错误：{str(exc)}"}), 500


@app.route("/task", methods=["DELETE"])
def clear_tasks():
    """清空全部任务列表数据。"""
    try:
        result = clear_task_history()
        return jsonify({"code": 200, "msg": "清空任务成功", "data": result}), 200
    except Exception as exc:
        logger.error(f"清空任务错误: {str(exc)}")
        return jsonify({"code": 500, "msg": f"服务器错误：{str(exc)}"}), 500


if __name__ == "__main__":
    try:
        init_database()
        logger.info("MySQL 数据库初始化完成")
    except Exception as exc:
        logger.error(f"MySQL 数据库初始化失败: {str(exc)}")
    app.run(host="0.0.0.0", port=5000, debug=True)
