import json
import logging
import os
import secrets
from contextlib import contextmanager
from datetime import datetime, timedelta

import pymysql


logger = logging.getLogger(__name__)
_db_initialized = False
TASK_STATUS_RUNNING = "分析中"
TASK_STATUS_DONE = "已完成"
TASK_STATUS_ERROR = "失败"


def get_db_settings() -> dict:
    """读取数据库配置，默认指向本地 MySQL。"""
    return {
        "host": os.getenv("DB_HOST", "localhost"),
        "port": int(os.getenv("DB_PORT", "3306")),
        "user": os.getenv("DB_USER", "root"),
        "password": os.getenv("DB_PASSWORD", "root"),
        "database": os.getenv("DB_NAME", "wdztcqhdfw"),
        "charset": "utf8mb4",
    }


def _build_connection(database_name: str | None = None):
    settings = get_db_settings()
    return pymysql.connect(
        host=settings["host"],
        port=settings["port"],
        user=settings["user"],
        password=settings["password"],
        database=database_name,
        charset=settings["charset"],
        autocommit=False,
        cursorclass=pymysql.cursors.DictCursor,
    )


@contextmanager
def get_connection(database_name: str | None = None):
    """统一管理数据库连接生命周期。"""
    connection = _build_connection(database_name)
    try:
        yield connection
    finally:
        connection.close()


def init_database():
    """创建数据库及所需表结构（仅保留当前在用的 7 张表）。"""
    global _db_initialized
    settings = get_db_settings()
    database_name = settings["database"]

    with get_connection(None) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"CREATE DATABASE IF NOT EXISTS `{database_name}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
        connection.commit()

    with get_connection(database_name) as connection:
        with connection.cursor() as cursor:
            # 用户表
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY AUTO_INCREMENT,
                    username VARCHAR(64) NOT NULL,
                    password_hash VARCHAR(255) NOT NULL,
                    is_admin TINYINT(1) NOT NULL DEFAULT 0,
                    create_time DATETIME NOT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE KEY uk_users_username (username)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """
            )
            # 分析任务表
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS analysis_tasks (
                    task_id BIGINT PRIMARY KEY AUTO_INCREMENT,
                    task_name VARCHAR(255) NOT NULL,
                    file_count INT NOT NULL DEFAULT 0,
                    theme_count INT NOT NULL DEFAULT 0,
                    task_status VARCHAR(32) NOT NULL DEFAULT '已完成',
                    create_time DATETIME NOT NULL,
                    user_id BIGINT NULL,
                    request_id VARCHAR(64) NULL,
                    request_payload_json JSON NULL,
                    response_payload_json JSON NULL,
                    tags_json JSON NULL,
                    is_archived TINYINT(1) NOT NULL DEFAULT 0,
                    progress INT NOT NULL DEFAULT 0,
                    parent_task_id BIGINT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_analysis_tasks_create_time (create_time),
                    INDEX idx_analysis_tasks_user_id (user_id),
                    UNIQUE KEY uk_analysis_tasks_user_request (user_id, request_id),
                    CONSTRAINT fk_analysis_tasks_user
                        FOREIGN KEY (user_id) REFERENCES users(user_id)
                        ON DELETE SET NULL
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """
            )
            # 文档表
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS document_info (
                    document_id BIGINT PRIMARY KEY AUTO_INCREMENT,
                    task_id BIGINT NOT NULL,
                    document_source_id VARCHAR(64) NOT NULL,
                    document_name VARCHAR(255) NOT NULL,
                    document_index INT NOT NULL,
                    document_content LONGTEXT NOT NULL,
                    word_count INT NOT NULL DEFAULT 0,
                    sentence_count INT NOT NULL DEFAULT 0,
                    language VARCHAR(32) NULL,
                    upload_time DATETIME NOT NULL,
                    sentences_json JSON NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE KEY uk_document_info_task_source (task_id, document_source_id),
                    INDEX idx_document_info_task_id (task_id),
                    INDEX idx_document_info_upload_time (upload_time),
                    CONSTRAINT fk_document_info_task
                        FOREIGN KEY (task_id) REFERENCES analysis_tasks(task_id)
                        ON DELETE CASCADE
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """
            )
            # 主题表
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS topic_info (
                    topic_id BIGINT PRIMARY KEY AUTO_INCREMENT,
                    task_id BIGINT NOT NULL,
                    document_id BIGINT NOT NULL,
                    topic_record_id VARCHAR(64) NOT NULL,
                    topic_name VARCHAR(255) NOT NULL,
                    topic_index INT NOT NULL,
                    summary TEXT NULL,
                    confidence FLOAT NULL,
                    score DOUBLE NOT NULL DEFAULT 0,
                    theme_evidence VARCHAR(1024) NOT NULL DEFAULT '',
                    is_confirmed TINYINT(1) NOT NULL DEFAULT 0,
                    create_time DATETIME NOT NULL,
                    topic_payload_json JSON NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE KEY uk_topic_info_document_topic (document_id, topic_index),
                    INDEX idx_topic_info_task_id (task_id),
                    INDEX idx_topic_info_document_id (document_id),
                    CONSTRAINT fk_topic_info_task
                        FOREIGN KEY (task_id) REFERENCES analysis_tasks(task_id)
                        ON DELETE CASCADE,
                    CONSTRAINT fk_topic_info_document
                        FOREIGN KEY (document_id) REFERENCES document_info(document_id)
                        ON DELETE CASCADE
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """
            )
            # 关键词表
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS keyword_info (
                    keyword_id BIGINT PRIMARY KEY AUTO_INCREMENT,
                    keyword_text VARCHAR(255) NOT NULL,
                    keyword_type VARCHAR(64) NULL,
                    create_time DATETIME NOT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE KEY uk_keyword_info_text (keyword_text)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """
            )
            # 关键词-主题关联表
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS topic_keyword_relations (
                    relation_id BIGINT PRIMARY KEY AUTO_INCREMENT,
                    topic_id BIGINT NOT NULL,
                    keyword_id BIGINT NOT NULL,
                    keyword_weight FLOAT NULL,
                    keyword_count INT NULL,
                    source_json JSON NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE KEY uk_topic_keyword_relation (topic_id, keyword_id),
                    INDEX idx_topic_keyword_topic_id (topic_id),
                    INDEX idx_topic_keyword_keyword_id (keyword_id),
                    CONSTRAINT fk_topic_keyword_topic
                        FOREIGN KEY (topic_id) REFERENCES topic_info(topic_id)
                        ON DELETE CASCADE,
                    CONSTRAINT fk_topic_keyword_keyword
                        FOREIGN KEY (keyword_id) REFERENCES keyword_info(keyword_id)
                        ON DELETE CASCADE
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """
            )
            # 任务统计表
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS task_statistics (
                    statistics_id BIGINT PRIMARY KEY AUTO_INCREMENT,
                    task_id BIGINT NOT NULL,
                    file_count INT NOT NULL DEFAULT 0,
                    theme_count INT NOT NULL DEFAULT 0,
                    doc_theme_count INT NOT NULL DEFAULT 0,
                    processing_time_ms FLOAT NULL,
                    algorithm_version VARCHAR(64) NULL,
                    create_time DATETIME NOT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE KEY uk_task_statistics_task_id (task_id),
                    CONSTRAINT fk_task_statistics_task
                        FOREIGN KEY (task_id) REFERENCES analysis_tasks(task_id)
                        ON DELETE CASCADE
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """
            )
            # 任务操作审计表只保存结构化元数据，不记录文档正文和请求内容。
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS task_audit_logs (
                    audit_id BIGINT PRIMARY KEY AUTO_INCREMENT,
                    task_id BIGINT NOT NULL,
                    user_id BIGINT NOT NULL,
                    action VARCHAR(64) NOT NULL,
                    detail_json JSON NULL,
                    create_time DATETIME NOT NULL,
                    INDEX idx_task_audit_task_id (task_id),
                    INDEX idx_task_audit_user_id (user_id),
                    CONSTRAINT fk_task_audit_task
                        FOREIGN KEY (task_id) REFERENCES analysis_tasks(task_id)
                        ON DELETE CASCADE,
                    CONSTRAINT fk_task_audit_user
                        FOREIGN KEY (user_id) REFERENCES users(user_id)
                        ON DELETE CASCADE
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS saved_task_filters (
                    filter_id BIGINT PRIMARY KEY AUTO_INCREMENT,
                    user_id BIGINT NOT NULL,
                    filter_name VARCHAR(100) NOT NULL,
                    filters_json JSON NOT NULL,
                    create_time DATETIME NOT NULL,
                    UNIQUE KEY uk_saved_filter_user_name (user_id, filter_name),
                    CONSTRAINT fk_saved_filter_user
                        FOREIGN KEY (user_id) REFERENCES users(user_id)
                        ON DELETE CASCADE
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS task_shares (
                    share_id BIGINT PRIMARY KEY AUTO_INCREMENT,
                    task_id BIGINT NOT NULL,
                    user_id BIGINT NOT NULL,
                    share_token VARCHAR(96) NOT NULL,
                    expires_at DATETIME NOT NULL,
                    create_time DATETIME NOT NULL,
                    UNIQUE KEY uk_task_share_token (share_token),
                    INDEX idx_task_share_task_id (task_id),
                    CONSTRAINT fk_task_share_task
                        FOREIGN KEY (task_id) REFERENCES analysis_tasks(task_id)
                        ON DELETE CASCADE,
                    CONSTRAINT fk_task_share_user
                        FOREIGN KEY (user_id) REFERENCES users(user_id)
                        ON DELETE CASCADE
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """
            )
            # 兼容旧库：历史库如果缺少任务状态列，启动时自动补齐。
            cursor.execute("SHOW COLUMNS FROM users LIKE 'is_admin'")
            if cursor.fetchone() is None:
                cursor.execute(
                    "ALTER TABLE users ADD COLUMN is_admin TINYINT(1) NOT NULL DEFAULT 0 AFTER password_hash"
                )
            cursor.execute("SHOW COLUMNS FROM analysis_tasks LIKE 'task_status'")
            if cursor.fetchone() is None:
                cursor.execute(
                    "ALTER TABLE analysis_tasks ADD COLUMN task_status VARCHAR(32) NOT NULL DEFAULT '已完成' AFTER theme_count"
                )
            # 兼容旧库：幂等键按用户唯一，NULL 仍允许历史任务共存。
            cursor.execute("SHOW COLUMNS FROM analysis_tasks LIKE 'request_id'")
            if cursor.fetchone() is None:
                cursor.execute(
                    "ALTER TABLE analysis_tasks ADD COLUMN request_id VARCHAR(64) NULL AFTER user_id"
                )
            cursor.execute(
                "SHOW INDEX FROM analysis_tasks WHERE Key_name = 'uk_analysis_tasks_user_request'"
            )
            if cursor.fetchone() is None:
                cursor.execute(
                    "ALTER TABLE analysis_tasks ADD UNIQUE KEY "
                    "uk_analysis_tasks_user_request (user_id, request_id)"
                )
            # 兼容旧库：逐列补齐任务管理 2.0 所需元数据。
            for column_name, column_sql in (
                ("tags_json", "JSON NULL AFTER response_payload_json"),
                ("is_archived", "TINYINT(1) NOT NULL DEFAULT 0 AFTER tags_json"),
                ("progress", "INT NOT NULL DEFAULT 0 AFTER is_archived"),
                ("parent_task_id", "BIGINT NULL AFTER progress"),
            ):
                cursor.execute(f"SHOW COLUMNS FROM analysis_tasks LIKE '{column_name}'")
                if cursor.fetchone() is None:
                    cursor.execute(
                        f"ALTER TABLE analysis_tasks ADD COLUMN {column_name} {column_sql}"
                    )
            cursor.execute("SHOW COLUMNS FROM topic_info LIKE 'is_confirmed'")
            if cursor.fetchone() is None:
                cursor.execute(
                    "ALTER TABLE topic_info ADD COLUMN is_confirmed "
                    "TINYINT(1) NOT NULL DEFAULT 0 AFTER theme_evidence"
                )
        connection.commit()
    _db_initialized = True


def ensure_database_ready():
    """懒初始化数据库，避免导入模式下遗漏建库。"""
    if not _db_initialized:
        init_database()


def _parse_upload_time(upload_time_str: str) -> datetime:
    return datetime.strptime(upload_time_str, "%Y-%m-%d %H:%M:%S")


def _first_request_file_name(request_payload: dict | None) -> str:
    """尽量从请求体中提取任务名称。"""
    if not isinstance(request_payload, dict):
        return ""
    file_names = request_payload.get("file_names")
    if isinstance(file_names, list):
        for name in file_names:
            clean_name = str(name).strip()
            if clean_name:
                return clean_name
    file_name = request_payload.get("file_name")
    return str(file_name).strip() if file_name else ""


def create_analysis_task_record(
    request_payload: dict | None,
    user_id: int | None = None,
    status: str = TASK_STATUS_RUNNING,
) -> int:
    """先落一条批次任务记录，便于前端看到分析中/失败状态。"""
    ensure_database_ready()
    settings = get_db_settings()
    now = datetime.now()
    task_name = _first_request_file_name(request_payload) or f"分析任务{now.strftime('%Y%m%d%H%M%S')}"
    file_count = 0
    if isinstance(request_payload, dict):
        raw_file_names = request_payload.get("file_names")
        if isinstance(raw_file_names, list):
            file_count = len(raw_file_names)
        elif isinstance(request_payload.get("texts"), list):
            file_count = len(request_payload.get("texts"))
        elif request_payload.get("text"):
            file_count = 1
    request_id = str((request_payload or {}).get("request_id", "") or "").strip() or None

    with get_connection(settings["database"]) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO analysis_tasks (
                    task_name, file_count, theme_count, task_status,
                    create_time, user_id, request_id, request_payload_json, response_payload_json,
                    progress, parent_task_id
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    task_name,
                    int(file_count),
                    0,
                    status,
                    now,
                    user_id,
                    request_id,
                    json.dumps(request_payload or {}, ensure_ascii=False),
                    None,
                    5,
                    int((request_payload or {}).get("_retry_source_task_id", 0) or 0) or None,
                ),
            )
            task_id = int(cursor.lastrowid)
        connection.commit()
        return task_id


def update_analysis_task_status(
    task_id: int,
    status: str,
    response_payload: dict | None = None,
) -> None:
    """更新任务状态和响应快照，失败信息也保留在数据库中。"""
    ensure_database_ready()
    settings = get_db_settings()
    with get_connection(settings["database"]) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE analysis_tasks
                SET task_status = %s,
                    response_payload_json = COALESCE(%s, response_payload_json),
                    progress = %s
                WHERE task_id = %s
                """,
                (
                    status,
                    json.dumps(response_payload, ensure_ascii=False) if response_payload is not None else None,
                    100 if status in {TASK_STATUS_DONE, TASK_STATUS_ERROR} else 5,
                    int(task_id),
                ),
            )
        connection.commit()


def update_analysis_task_progress(task_id: int, user_id: int, progress: int) -> None:
    """更新运行中任务进度，限制在任务所有者范围内。"""
    ensure_database_ready()
    settings = get_db_settings()
    normalized_progress = min(99, max(0, int(progress)))
    with get_connection(settings["database"]) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE analysis_tasks
                SET progress = %s
                WHERE task_id = %s AND user_id = %s AND task_status = %s
                """,
                (normalized_progress, int(task_id), int(user_id), TASK_STATUS_RUNNING),
            )
        connection.commit()


def _insert_normalized_extract_result(
    cursor,
    extract_result: dict,
    statistics: dict,
    request_payload: dict | None,
    response_payload: dict | None,
    user_id: int | None = None,
    existing_task_id: int | None = None,
) -> int:
    """按六实体模型保存分析结果。"""
    now = datetime.now()
    task_name = _first_request_file_name(request_payload) or f"分析任务{now.strftime('%Y%m%d%H%M%S')}"
    if existing_task_id:
        task_id = int(existing_task_id)
        cursor.execute(
            """
            UPDATE analysis_tasks
            SET task_name = %s,
                file_count = %s,
                theme_count = %s,
                task_status = %s,
                user_id = %s,
                request_payload_json = %s,
                response_payload_json = %s,
                progress = 100
            WHERE task_id = %s
            """,
            (
                task_name,
                int(statistics.get("file_count", 0)),
                int(statistics.get("theme_count", 0)),
                TASK_STATUS_DONE,
                user_id,
                json.dumps(request_payload or {}, ensure_ascii=False),
                json.dumps(response_payload or {}, ensure_ascii=False),
                task_id,
            ),
        )
    else:
        request_id = str((request_payload or {}).get("request_id", "") or "").strip() or None
        cursor.execute(
            """
            INSERT INTO analysis_tasks (
                task_name, file_count, theme_count, task_status,
                create_time, user_id, request_id, request_payload_json, response_payload_json,
                progress
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 100)
            """,
            (
                task_name,
                int(statistics.get("file_count", 0)),
                int(statistics.get("theme_count", 0)),
                TASK_STATUS_DONE,
                now,
                user_id,
                request_id,
                json.dumps(request_payload or {}, ensure_ascii=False),
                json.dumps(response_payload or {}, ensure_ascii=False),
            ),
        )
        task_id = int(cursor.lastrowid)

    doc_items_by_file_id = {str(item.get("file_id", "")): item for item in extract_result.get("doc_items", [])}
    document_id_by_source_id = {}
    saved_document_ids = []
    for file_info in extract_result.get("files", []):
        source_id = str(file_info.get("id", ""))
        doc_item = doc_items_by_file_id.get(source_id, {})
        cursor.execute(
            """
            INSERT INTO document_info (
                task_id, document_source_id, document_name, document_index,
                document_content, word_count, sentence_count, language,
                upload_time, sentences_json
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                task_id,
                source_id,
                str(file_info.get("name", "")),
                int(file_info.get("index", 0)),
                str(doc_item.get("text", "")),
                int(file_info.get("word_count", 0)),
                int(file_info.get("sentence_count", 0)),
                str(file_info.get("language", "")),
                _parse_upload_time(str(file_info.get("upload_time", ""))),
                json.dumps(doc_item.get("sentences", []), ensure_ascii=False),
            ),
        )
        saved_document_id = int(cursor.lastrowid)
        document_id_by_source_id[source_id] = saved_document_id
        saved_document_ids.append(saved_document_id)

    for theme_info in extract_result.get("doc_themes", []):
        source_id = str(theme_info.get("file_id", ""))
        document_id = document_id_by_source_id.get(source_id)
        if not document_id:
            continue
        cursor.execute(
            """
            INSERT INTO topic_info (
                task_id, document_id, topic_record_id, topic_name,
                topic_index, summary, confidence, score,
                theme_evidence, create_time, topic_payload_json
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                task_id,
                document_id,
                str(theme_info.get("id", "")),
                str(theme_info.get("theme", "")),
                int(theme_info.get("topic_index", 0)),
                str(theme_info.get("summary", "")),
                float(theme_info.get("confidence", 0)),
                float(theme_info.get("score", 0)),
                str(theme_info.get("theme_evidence", "")),
                now,
                json.dumps(theme_info, ensure_ascii=False),
            ),
        )
        topic_id = int(cursor.lastrowid)

        for detail in theme_info.get("keyword_details", []) or []:
            keyword_text = str(detail.get("text", "")).strip()
            if not keyword_text:
                continue
            cursor.execute(
                "INSERT INTO keyword_info (keyword_text, keyword_type, create_time) VALUES (%s, %s, %s) ON DUPLICATE KEY UPDATE keyword_text = VALUES(keyword_text)",
                (keyword_text, None, now),
            )
            cursor.execute("SELECT keyword_id FROM keyword_info WHERE keyword_text = %s", (keyword_text,))
            keyword_row = cursor.fetchone() or {}
            keyword_id = int(keyword_row.get("keyword_id", 0))
            if not keyword_id:
                continue
            cursor.execute(
                """
                INSERT INTO topic_keyword_relations (topic_id, keyword_id, keyword_weight, keyword_count, source_json)
                VALUES (%s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE keyword_weight = VALUES(keyword_weight), keyword_count = VALUES(keyword_count), source_json = VALUES(source_json)
                """,
                (
                    topic_id,
                    keyword_id,
                    float(detail.get("weight", 0)),
                    int(detail.get("count", 0)),
                    json.dumps(detail.get("source", {}), ensure_ascii=False),
                ),
            )

    cursor.execute(
        """
        INSERT INTO task_statistics (task_id, file_count, theme_count, doc_theme_count, processing_time_ms, algorithm_version, create_time)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (
            task_id,
            int(statistics.get("file_count", 0)),
            int(statistics.get("theme_count", 0)),
            int(statistics.get("doc_theme_count", 0)),
            float(statistics.get("processing_time_ms", 0)),
            str(statistics.get("algorithm_version", "")),
            now,
        ),
    )
    return {
        "batch_id": task_id,
        "document_task_ids": saved_document_ids,
        "task_id": task_id,
        "selected_document_id": saved_document_ids[0] if saved_document_ids else None,
    }


def save_extract_result(
    extract_result: dict,
    statistics: dict,
    request_payload: dict | None = None,
    response_payload: dict | None = None,
    user_id: int | None = None,
    existing_task_id: int | None = None,
):
    """保存一次 /extract 的处理结果到新表。"""
    ensure_database_ready()
    settings = get_db_settings()
    with get_connection(settings["database"]) as connection:
        with connection.cursor() as cursor:
            saved_info = _insert_normalized_extract_result(
                cursor,
                extract_result,
                statistics,
                request_payload,
                response_payload,
                user_id=user_id,
                existing_task_id=existing_task_id,
            )
        connection.commit()
        return saved_info


def save_wordcloud_result(request_payload: dict, response_payload: dict):
    """词云历史记录（已切换到新表存储）。"""
    pass  # 词云数据已存储在新表的 response_payload_json 中


def find_existing_document_names(file_names: list[str], user_id: int) -> list[str]:
    """只在当前用户的历史任务中检查重复文档名。"""
    clean_names = sorted({str(name).strip() for name in file_names if str(name).strip()})
    if not clean_names:
        return []

    ensure_database_ready()
    settings = get_db_settings()
    placeholders = ", ".join(["%s"] * len(clean_names))
    with get_connection(settings["database"]) as connection:
        with connection.cursor() as cursor:
            existed_names = set()
            cursor.execute(
                f"""
                SELECT di.document_name AS name
                FROM document_info di
                INNER JOIN analysis_tasks at ON at.task_id = di.task_id
                WHERE at.user_id = %s
                  AND di.document_name IN ({placeholders})
                """,
                (int(user_id), *clean_names),
            )
            existed_names.update(str(row.get("name", "")) for row in cursor.fetchall())
    return [name for name in clean_names if name in existed_names]


def query_task_page(
    user_id: int,
    *,
    page: int = 1,
    page_size: int = 20,
    keyword: str = "",
    status: str = "all",
    days: int = 0,
    sort_order: str = "newest",
    focus_task_id: int | None = None,
    archived: str = "active",
) -> dict:
    """在用户隔离范围内完成任务筛选、排序、分页和定位页计算。"""
    ensure_database_ready()
    settings = get_db_settings()
    status_map = {
        "done": TASK_STATUS_DONE,
        "running": TASK_STATUS_RUNNING,
        "error": TASK_STATUS_ERROR,
    }
    where_parts = ["at.user_id = %s"]
    params: list = [int(user_id)]
    clean_keyword = str(keyword or "").strip()
    if clean_keyword:
        like_keyword = f"%{clean_keyword}%"
        where_parts.append(
            "(at.task_name LIKE %s OR CAST(at.task_id AS CHAR) LIKE %s OR at.task_status LIKE %s)"
        )
        params.extend([like_keyword, like_keyword, like_keyword])
    if status in status_map:
        where_parts.append("at.task_status = %s")
        params.append(status_map[status])
    if int(days or 0) > 0:
        where_parts.append("at.create_time >= DATE_SUB(NOW(), INTERVAL %s DAY)")
        params.append(int(days))
    if archived == "archived":
        where_parts.append("at.is_archived = 1")
    elif archived != "all":
        where_parts.append("at.is_archived = 0")

    where_sql = " AND ".join(where_parts)
    direction = "ASC" if sort_order == "oldest" else "DESC"
    normalized_page = max(int(page), 1)
    normalized_page_size = min(max(int(page_size), 1), 100)

    with get_connection(settings["database"]) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT COUNT(*) AS total FROM analysis_tasks at WHERE {where_sql}",
                tuple(params),
            )
            total = int((cursor.fetchone() or {}).get("total", 0) or 0)
            total_pages = max((total + normalized_page_size - 1) // normalized_page_size, 1)
            normalized_page = min(normalized_page, total_pages)

            focus_page = None
            if focus_task_id:
                cursor.execute(
                    f"SELECT at.create_time, at.task_id FROM analysis_tasks at "
                    f"WHERE {where_sql} AND at.task_id = %s LIMIT 1",
                    (*params, int(focus_task_id)),
                )
                focus_row = cursor.fetchone()
                if focus_row:
                    comparator = ">" if direction == "DESC" else "<"
                    id_comparator = ">" if direction == "DESC" else "<"
                    cursor.execute(
                        f"SELECT COUNT(*) AS before_count FROM analysis_tasks at "
                        f"WHERE {where_sql} AND "
                        f"(at.create_time {comparator} %s OR "
                        f"(at.create_time = %s AND at.task_id {id_comparator} %s))",
                        (*params, focus_row["create_time"], focus_row["create_time"], int(focus_task_id)),
                    )
                    before_count = int((cursor.fetchone() or {}).get("before_count", 0) or 0)
                    focus_page = before_count // normalized_page_size + 1

            offset = (normalized_page - 1) * normalized_page_size
            cursor.execute(
                f"""
                SELECT
                    at.task_id,
                    at.task_id AS batch_id,
                    at.task_id AS id,
                    at.task_name AS name,
                    0 AS `index`,
                    COALESCE(SUM(di.word_count), 0) AS word_count,
                    COALESCE(SUM(di.sentence_count), 0) AS sentence_count,
                    COALESCE(
                        GROUP_CONCAT(DISTINCT NULLIF(di.language, '') ORDER BY di.language SEPARATOR '/'),
                        ''
                    ) AS language,
                    DATE_FORMAT(
                        COALESCE(MAX(di.upload_time), at.create_time),
                        '%%Y-%%m-%%d %%H:%%i:%%s'
                    ) AS upload_time,
                    at.file_count AS doc_count,
                    at.task_status AS status,
                    at.task_status AS task_status,
                    at.tags_json,
                    at.is_archived,
                    at.progress,
                    at.parent_task_id
                FROM analysis_tasks at
                LEFT JOIN document_info di ON di.task_id = at.task_id
                WHERE {where_sql}
                GROUP BY at.task_id, at.task_name, at.file_count, at.task_status, at.create_time,
                    at.tags_json, at.is_archived, at.progress, at.parent_task_id
                ORDER BY at.create_time {direction}, at.task_id {direction}
                LIMIT %s OFFSET %s
                """,
                (*params, normalized_page_size, offset),
            )
            items = list(cursor.fetchall())

    for item in items:
        item["tags"] = _parse_json_field(item.pop("tags_json", None), [])
        item["archived"] = bool(item.pop("is_archived", 0))
        item["progress"] = min(100, max(0, int(item.get("progress", 0) or 0)))

    return {
        "items": items,
        "pagination": {
            "page": normalized_page,
            "page_size": normalized_page_size,
            "total": total,
            "total_pages": total_pages,
        },
        "focus_page": focus_page,
    }


def fetch_task_summary(user_id: int) -> dict:
    """按批次统计当前用户全部任务，避免列表截断影响统计口径。"""
    ensure_database_ready()
    settings = get_db_settings()
    with get_connection(settings["database"]) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    COUNT(*) AS total_count,
                    SUM(CASE WHEN task_status = %s THEN 1 ELSE 0 END) AS done_count,
                    SUM(CASE WHEN task_status = %s THEN 1 ELSE 0 END) AS running_count,
                    SUM(CASE WHEN task_status = %s THEN 1 ELSE 0 END) AS error_count,
                    SUM(CASE WHEN is_archived = 1 THEN 1 ELSE 0 END) AS archived_count,
                    COALESCE(SUM(file_count), 0) AS document_count
                FROM analysis_tasks
                WHERE user_id = %s
                """,
                (TASK_STATUS_DONE, TASK_STATUS_RUNNING, TASK_STATUS_ERROR, int(user_id)),
            )
            row = cursor.fetchone() or {}
            return {
                "total_count": int(row.get("total_count", 0) or 0),
                "done_count": int(row.get("done_count", 0) or 0),
                "running_count": int(row.get("running_count", 0) or 0),
                "error_count": int(row.get("error_count", 0) or 0),
                "archived_count": int(row.get("archived_count", 0) or 0),
                "document_count": int(row.get("document_count", 0) or 0),
            }


def _append_task_audit(cursor, task_id: int, user_id: int, action: str, detail: dict | None = None):
    """在同一事务中追加任务操作记录。"""
    cursor.execute(
        """
        INSERT INTO task_audit_logs (task_id, user_id, action, detail_json, create_time)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (
            int(task_id),
            int(user_id),
            str(action),
            json.dumps(detail or {}, ensure_ascii=False),
            datetime.now(),
        ),
    )


def update_task_metadata(
    task_id: int,
    user_id: int,
    *,
    name: str | None = None,
    tags: list[str] | None = None,
    archived: bool | None = None,
) -> dict | None:
    """集中更新任务名称、标签和归档状态，并记录审计日志。"""
    ensure_database_ready()
    settings = get_db_settings()
    updates = []
    params = []
    detail = {}
    if name is not None:
        updates.append("task_name = %s")
        params.append(str(name))
        detail["name"] = str(name)
    if tags is not None:
        updates.append("tags_json = %s")
        params.append(json.dumps(tags, ensure_ascii=False))
        detail["tags"] = tags
    if archived is not None:
        updates.append("is_archived = %s")
        params.append(1 if archived else 0)
        detail["archived"] = bool(archived)
    if not updates:
        return None

    with get_connection(settings["database"]) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"UPDATE analysis_tasks SET {', '.join(updates)} "
                "WHERE task_id = %s AND user_id = %s",
                (*params, int(task_id), int(user_id)),
            )
            if cursor.rowcount <= 0:
                return None
            _append_task_audit(cursor, task_id, user_id, "update_metadata", detail)
            cursor.execute(
                """
                SELECT task_id, task_name, tags_json, is_archived, progress, parent_task_id
                FROM analysis_tasks
                WHERE task_id = %s AND user_id = %s
                LIMIT 1
                """,
                (int(task_id), int(user_id)),
            )
            row = cursor.fetchone() or {}
        connection.commit()

    return {
        "task_id": int(row.get("task_id", task_id)),
        "name": str(row.get("task_name", name or "")),
        "tags": _parse_json_field(row.get("tags_json"), []),
        "archived": bool(row.get("is_archived", 0)),
        "progress": int(row.get("progress", 0) or 0),
        "parent_task_id": row.get("parent_task_id"),
    }


def batch_update_tasks(
    user_id: int,
    task_ids: list[int],
    action: str,
    *,
    tags: list[str] | None = None,
) -> dict:
    """在当前用户范围内执行归档、恢复、打标签或删除操作。"""
    ensure_database_ready()
    settings = get_db_settings()
    normalized_ids = list(dict.fromkeys(int(task_id) for task_id in task_ids))
    if not normalized_ids:
        return {"action": action, "affected_count": 0, "task_ids": []}

    placeholders = ", ".join(["%s"] * len(normalized_ids))
    with get_connection(settings["database"]) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT task_id, tags_json FROM analysis_tasks "
                f"WHERE user_id = %s AND task_id IN ({placeholders})",
                (int(user_id), *normalized_ids),
            )
            owned_rows = list(cursor.fetchall())
            owned_ids = [int(row["task_id"]) for row in owned_rows]
            if not owned_ids:
                return {"action": action, "affected_count": 0, "task_ids": []}

            owned_placeholders = ", ".join(["%s"] * len(owned_ids))
            if action in {"archive", "restore"}:
                archived_value = 1 if action == "archive" else 0
                cursor.execute(
                    f"UPDATE analysis_tasks SET is_archived = %s "
                    f"WHERE user_id = %s AND task_id IN ({owned_placeholders})",
                    (archived_value, int(user_id), *owned_ids),
                )
                for task_id in owned_ids:
                    _append_task_audit(
                        cursor,
                        task_id,
                        user_id,
                        action,
                        {"archived": bool(archived_value)},
                    )
            elif action == "tag":
                clean_tags = list(dict.fromkeys(tags or []))
                row_map = {int(row["task_id"]): row for row in owned_rows}
                for task_id in owned_ids:
                    current_tags = _parse_json_field(row_map[task_id].get("tags_json"), [])
                    merged_tags = list(dict.fromkeys([*current_tags, *clean_tags]))[:10]
                    cursor.execute(
                        "UPDATE analysis_tasks SET tags_json = %s "
                        "WHERE task_id = %s AND user_id = %s",
                        (json.dumps(merged_tags, ensure_ascii=False), task_id, int(user_id)),
                    )
                    _append_task_audit(cursor, task_id, user_id, action, {"tags": merged_tags})
            elif action == "delete":
                cursor.execute(
                    f"DELETE FROM analysis_tasks WHERE user_id = %s "
                    f"AND task_id IN ({owned_placeholders})",
                    (int(user_id), *owned_ids),
                )
                _delete_orphan_keywords(cursor)
            else:
                raise ValueError("不支持的批量操作")
        connection.commit()

    return {
        "action": action,
        "affected_count": len(owned_ids),
        "task_ids": owned_ids,
    }


def copy_task(task_id: int, user_id: int) -> dict | None:
    """复制完整分析批次，规范化文档、主题和关键词关系随批次一起复制。"""
    ensure_database_ready()
    settings = get_db_settings()
    now = datetime.now()
    with get_connection(settings["database"]) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM analysis_tasks WHERE task_id = %s AND user_id = %s LIMIT 1",
                (int(task_id), int(user_id)),
            )
            source = cursor.fetchone()
            if not source:
                return None

            copy_name = f"{str(source.get('task_name') or '分析任务')} - 副本"
            cursor.execute(
                """
                INSERT INTO analysis_tasks (
                    task_name, file_count, theme_count, task_status, create_time,
                    user_id, request_id, request_payload_json, response_payload_json,
                    tags_json, is_archived, progress, parent_task_id
                ) VALUES (%s, %s, %s, %s, %s, %s, NULL, %s, %s, %s, 0, %s, %s)
                """,
                (
                    copy_name,
                    int(source.get("file_count", 0) or 0),
                    int(source.get("theme_count", 0) or 0),
                    str(source.get("task_status") or TASK_STATUS_DONE),
                    now,
                    int(user_id),
                    source.get("request_payload_json"),
                    source.get("response_payload_json"),
                    source.get("tags_json"),
                    int(source.get("progress", 100) or 0),
                    int(task_id),
                ),
            )
            new_task_id = int(cursor.lastrowid)

            cursor.execute(
                "SELECT * FROM document_info WHERE task_id = %s ORDER BY document_id",
                (int(task_id),),
            )
            source_documents = list(cursor.fetchall())
            document_id_map = {}
            for document in source_documents:
                cursor.execute(
                    """
                    INSERT INTO document_info (
                        task_id, document_source_id, document_name, document_index,
                        document_content, word_count, sentence_count, language,
                        upload_time, sentences_json
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        new_task_id,
                        document.get("document_source_id"),
                        document.get("document_name"),
                        document.get("document_index"),
                        document.get("document_content"),
                        document.get("word_count"),
                        document.get("sentence_count"),
                        document.get("language"),
                        document.get("upload_time"),
                        document.get("sentences_json"),
                    ),
                )
                document_id_map[int(document["document_id"])] = int(cursor.lastrowid)

            cursor.execute(
                "SELECT * FROM topic_info WHERE task_id = %s ORDER BY topic_id",
                (int(task_id),),
            )
            source_topics = list(cursor.fetchall())
            for topic in source_topics:
                source_topic_id = int(topic["topic_id"])
                cursor.execute(
                    """
                    INSERT INTO topic_info (
                        task_id, document_id, topic_record_id, topic_name, topic_index,
                        summary, confidence, score, theme_evidence, is_confirmed,
                        create_time, topic_payload_json
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        new_task_id,
                        document_id_map[int(topic["document_id"])],
                        topic.get("topic_record_id"),
                        topic.get("topic_name"),
                        topic.get("topic_index"),
                        topic.get("summary"),
                        topic.get("confidence"),
                        topic.get("score"),
                        topic.get("theme_evidence"),
                        int(topic.get("is_confirmed", 0) or 0),
                        now,
                        topic.get("topic_payload_json"),
                    ),
                )
                new_topic_id = int(cursor.lastrowid)
                cursor.execute(
                    """
                    INSERT INTO topic_keyword_relations (
                        topic_id, keyword_id, keyword_weight, keyword_count, source_json
                    )
                    SELECT %s, keyword_id, keyword_weight, keyword_count, source_json
                    FROM topic_keyword_relations
                    WHERE topic_id = %s
                    """,
                    (new_topic_id, source_topic_id),
                )

            cursor.execute(
                """
                INSERT INTO task_statistics (
                    task_id, file_count, theme_count, doc_theme_count,
                    processing_time_ms, algorithm_version, create_time
                )
                SELECT %s, file_count, theme_count, doc_theme_count,
                    processing_time_ms, algorithm_version, %s
                FROM task_statistics
                WHERE task_id = %s
                """,
                (new_task_id, now, int(task_id)),
            )
            _append_task_audit(cursor, task_id, user_id, "copy_source", {"copy_task_id": new_task_id})
            _append_task_audit(cursor, new_task_id, user_id, "copied_from", {"source_task_id": int(task_id)})
        connection.commit()

    return {
        "task_id": new_task_id,
        "parent_task_id": int(task_id),
        "name": copy_name,
    }


def get_task_retry_payload(task_id: int, user_id: int) -> dict | None:
    """读取当前用户失败任务的原始请求，清理旧幂等键后用于重新提交。"""
    ensure_database_ready()
    settings = get_db_settings()
    with get_connection(settings["database"]) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT request_payload_json
                FROM analysis_tasks
                WHERE task_id = %s AND user_id = %s AND task_status = %s
                LIMIT 1
                """,
                (int(task_id), int(user_id), TASK_STATUS_ERROR),
            )
            row = cursor.fetchone()
            if not row:
                return None
            payload = _parse_json_field(row.get("request_payload_json"), {})
            if not isinstance(payload, dict) or not payload:
                return None
            payload = dict(payload)
            payload.pop("request_id", None)
            payload.pop("username", None)
            payload["record_recent"] = True
            payload["_retry_source_task_id"] = int(task_id)
            _append_task_audit(cursor, task_id, user_id, "retry_requested")
        connection.commit()
    return payload


def fetch_task_audit(task_id: int, user_id: int) -> list[dict] | None:
    """读取当前用户任务的操作审计记录。"""
    ensure_database_ready()
    settings = get_db_settings()
    with get_connection(settings["database"]) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT task_id FROM analysis_tasks WHERE task_id = %s AND user_id = %s LIMIT 1",
                (int(task_id), int(user_id)),
            )
            if not cursor.fetchone():
                return None
            cursor.execute(
                """
                SELECT audit_id, action, detail_json,
                    DATE_FORMAT(create_time, '%%Y-%%m-%%d %%H:%%i:%%s') AS create_time
                FROM task_audit_logs
                WHERE task_id = %s AND user_id = %s
                ORDER BY audit_id DESC
                LIMIT 100
                """,
                (int(task_id), int(user_id)),
            )
            rows = list(cursor.fetchall())
    for row in rows:
        row["detail"] = _parse_json_field(row.pop("detail_json", None), {})
    return rows


def fetch_admin_statistics() -> dict:
    """返回不含正文和身份敏感字段的系统聚合统计。"""
    ensure_database_ready()
    settings = get_db_settings()
    with get_connection(settings["database"]) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM users) AS user_count,
                    (SELECT COUNT(*) FROM analysis_tasks) AS task_count,
                    (SELECT COUNT(*) FROM document_info) AS document_count,
                    (SELECT COUNT(*) FROM analysis_tasks WHERE task_status = %s) AS done_count,
                    (SELECT COUNT(*) FROM analysis_tasks WHERE task_status = %s) AS running_count,
                    (SELECT COUNT(*) FROM analysis_tasks WHERE task_status = %s) AS error_count,
                    (SELECT COUNT(*) FROM analysis_tasks WHERE is_archived = 1) AS archived_count
                """,
                (TASK_STATUS_DONE, TASK_STATUS_RUNNING, TASK_STATUS_ERROR),
            )
            row = cursor.fetchone() or {}
    return {key: int(value or 0) for key, value in row.items()}


def fetch_task_comparison_snapshots(user_id: int, task_ids: list[int]) -> list[dict]:
    """按传入顺序读取当前用户任务的主题与质量快照。"""
    ensure_database_ready()
    settings = get_db_settings()
    normalized_ids = list(dict.fromkeys(int(task_id) for task_id in task_ids))
    snapshots = []
    with get_connection(settings["database"]) as connection:
        with connection.cursor() as cursor:
            for task_id in normalized_ids:
                cursor.execute(
                    """
                    SELECT at.task_id, at.task_name, at.response_payload_json,
                        ts.algorithm_version
                    FROM analysis_tasks at
                    LEFT JOIN task_statistics ts ON ts.task_id = at.task_id
                    WHERE at.task_id = %s AND at.user_id = %s
                    LIMIT 1
                    """,
                    (task_id, int(user_id)),
                )
                row = cursor.fetchone()
                if not row:
                    continue
                response_payload = _parse_json_field(row.get("response_payload_json"), {})
                response_data = response_payload.get("data", {}) if isinstance(response_payload, dict) else {}
                statistics = response_data.get("statistics", {}) if isinstance(response_data, dict) else {}
                snapshots.append({
                    "task_id": int(row["task_id"]),
                    "name": str(row.get("task_name") or ""),
                    "algorithm_version": str(
                        row.get("algorithm_version")
                        or statistics.get("algorithm_version")
                        or ""
                    ),
                    "quality_metrics": statistics.get("quality_metrics", {}),
                    "themes": _fetch_normalized_task_topics(cursor, task_id),
                })
    return snapshots


def save_task_filter(user_id: int, name: str, filters: dict) -> dict:
    """保存或覆盖当前用户的任务筛选条件。"""
    ensure_database_ready()
    settings = get_db_settings()
    now = datetime.now()
    with get_connection(settings["database"]) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO saved_task_filters (user_id, filter_name, filters_json, create_time)
                VALUES (%s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE filters_json = VALUES(filters_json), create_time = VALUES(create_time)
                """,
                (int(user_id), str(name), json.dumps(filters, ensure_ascii=False), now),
            )
            cursor.execute(
                """
                SELECT filter_id FROM saved_task_filters
                WHERE user_id = %s AND filter_name = %s
                LIMIT 1
                """,
                (int(user_id), str(name)),
            )
            row = cursor.fetchone() or {}
        connection.commit()
    return {"filter_id": int(row.get("filter_id", 0)), "name": str(name), "filters": filters}


def list_task_filters(user_id: int) -> list[dict]:
    """列出当前用户保存的筛选条件。"""
    ensure_database_ready()
    settings = get_db_settings()
    with get_connection(settings["database"]) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT filter_id, filter_name, filters_json,
                    DATE_FORMAT(create_time, '%%Y-%%m-%%d %%H:%%i:%%s') AS create_time
                FROM saved_task_filters
                WHERE user_id = %s
                ORDER BY create_time DESC, filter_id DESC
                """,
                (int(user_id),),
            )
            rows = list(cursor.fetchall())
    return [{
        "filter_id": int(row["filter_id"]),
        "name": str(row.get("filter_name") or ""),
        "filters": _parse_json_field(row.get("filters_json"), {}),
        "create_time": str(row.get("create_time") or ""),
    } for row in rows]


def delete_task_filter(filter_id: int, user_id: int) -> bool:
    """删除当前用户的一条保存筛选。"""
    ensure_database_ready()
    settings = get_db_settings()
    with get_connection(settings["database"]) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM saved_task_filters WHERE filter_id = %s AND user_id = %s",
                (int(filter_id), int(user_id)),
            )
            deleted = cursor.rowcount > 0
        connection.commit()
    return deleted


def create_task_share(task_id: int, user_id: int, expires_days: int) -> dict | None:
    """为当前用户任务创建高熵只读分享令牌。"""
    ensure_database_ready()
    settings = get_db_settings()
    now = datetime.now()
    expires_at = now + timedelta(days=int(expires_days))
    share_token = secrets.token_urlsafe(32)
    with get_connection(settings["database"]) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT task_id FROM analysis_tasks WHERE task_id = %s AND user_id = %s LIMIT 1",
                (int(task_id), int(user_id)),
            )
            if not cursor.fetchone():
                return None
            cursor.execute(
                """
                INSERT INTO task_shares (task_id, user_id, share_token, expires_at, create_time)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (int(task_id), int(user_id), share_token, expires_at, now),
            )
            _append_task_audit(
                cursor,
                task_id,
                user_id,
                "create_share",
                {"expires_at": expires_at.strftime("%Y-%m-%d %H:%M:%S")},
            )
        connection.commit()
    return {"token": share_token, "expires_at": expires_at.strftime("%Y-%m-%d %H:%M:%S")}


def fetch_shared_task(share_token: str) -> dict | None:
    """通过未过期令牌读取脱敏后的只读任务详情。"""
    ensure_database_ready()
    settings = get_db_settings()
    with get_connection(settings["database"]) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT task_id, user_id, expires_at
                FROM task_shares
                WHERE share_token = %s AND expires_at > NOW()
                LIMIT 1
                """,
                (str(share_token),),
            )
            row = cursor.fetchone()
    if not row:
        return None
    detail = fetch_task_detail(int(row["task_id"]), int(row["user_id"]))
    if not detail:
        return None
    data = detail.get("data", {})
    for file_info in data.get("files", []) if isinstance(data, dict) else []:
        file_info.pop("content", None)
        file_info.pop("raw_text", None)
        file_info.pop("sentences", None)
        file_info.pop("task_id", None)
    data["read_only"] = True
    data["share_expires_at"] = row["expires_at"].strftime("%Y-%m-%d %H:%M:%S")
    return detail


def _parse_json_field(value, default):
    """兼容 MySQL JSON 字段返回字符串或原生对象两种情况。"""
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return default
    return default


def find_task_by_request_id(user_id: int, request_id: str):
    """按当前用户和幂等键读取已有批次，防止超时重试重复写入。"""
    ensure_database_ready()
    settings = get_db_settings()
    with get_connection(settings["database"]) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT task_id, task_status, response_payload_json
                FROM analysis_tasks
                WHERE user_id = %s AND request_id = %s
                LIMIT 1
                """,
                (int(user_id), str(request_id)),
            )
            row = cursor.fetchone()
            if not row:
                return None
            return {
                "task_id": int(row["task_id"]),
                "task_status": str(row.get("task_status", "")),
                "response_payload": _parse_json_field(row.get("response_payload_json"), {}),
            }


def _delete_orphan_keywords(cursor):
    """删除不再被任何主题引用的关键词。"""
    cursor.execute(
        """
        DELETE ki FROM keyword_info ki
        LEFT JOIN topic_keyword_relations tkr ON tkr.keyword_id = ki.keyword_id
        WHERE tkr.keyword_id IS NULL
        """
    )


def _fetch_normalized_task_topics(cursor, task_id: int) -> list[dict]:
    """从规范化主题表重建详情，确保编辑结果覆盖初始响应快照。"""
    cursor.execute(
        """
        SELECT
            ti.topic_id,
            ti.topic_record_id,
            ti.topic_name,
            ti.topic_index,
            ti.summary,
            ti.confidence,
            ti.score,
            ti.theme_evidence,
            ti.is_confirmed,
            ti.topic_payload_json,
            di.document_source_id AS file_id,
            di.document_index AS file_index,
            di.document_name AS file_name,
            ki.keyword_text,
            tkr.keyword_weight,
            tkr.keyword_count,
            tkr.source_json
        FROM topic_info ti
        INNER JOIN document_info di ON di.document_id = ti.document_id
        LEFT JOIN topic_keyword_relations tkr ON tkr.topic_id = ti.topic_id
        LEFT JOIN keyword_info ki ON ki.keyword_id = tkr.keyword_id
        WHERE ti.task_id = %s
        ORDER BY di.document_index ASC, ti.topic_index ASC, tkr.keyword_weight DESC, ki.keyword_id ASC
        """,
        (int(task_id),),
    )
    topic_map = {}
    for row in cursor.fetchall():
        topic_id = int(row["topic_id"])
        if topic_id not in topic_map:
            payload = _parse_json_field(row.get("topic_payload_json"), {})
            item = dict(payload) if isinstance(payload, dict) else {}
            item.update({
                "id": str(row.get("topic_record_id") or topic_id),
                "topic_id": topic_id,
                "topic_record_id": str(row.get("topic_record_id") or topic_id),
                "theme": str(row.get("topic_name") or "未命名主题"),
                "topic_index": int(row.get("topic_index", 0) or 0),
                "summary": str(row.get("summary") or ""),
                "confidence": float(row.get("confidence", 0) or 0),
                "score": float(row.get("score", 0) or 0),
                "theme_evidence": str(row.get("theme_evidence") or ""),
                "confirmed": bool(row.get("is_confirmed", 0)),
                "file_id": str(row.get("file_id") or ""),
                "file_index": int(row.get("file_index", 0) or 0),
                "file_name": str(row.get("file_name") or ""),
                "keywords": [],
                "keyword_details": [],
            })
            topic_map[topic_id] = item
        keyword_text = str(row.get("keyword_text") or "").strip()
        if keyword_text:
            detail = {
                "text": keyword_text,
                "weight": float(row.get("keyword_weight", 0) or 0),
                "count": int(row.get("keyword_count", 0) or 0),
                "source": _parse_json_field(row.get("source_json"), {}),
            }
            topic_map[topic_id]["keywords"].append(keyword_text)
            topic_map[topic_id]["keyword_details"].append(detail)
    return list(topic_map.values())


def fetch_task_detail(task_id: int, user_id: int) -> dict | None:
    """按任务主键读取当前用户的整批分析结果。"""
    ensure_database_ready()
    settings = get_db_settings()
    with get_connection(settings["database"]) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    at.task_id,
                    at.task_id AS batch_id,
                    at.file_count AS batch_file_count,
                    at.theme_count AS batch_theme_count,
                    at.task_status,
                    at.response_payload_json,
                    ts.doc_theme_count AS batch_doc_theme_count,
                    ts.processing_time_ms,
                    ts.algorithm_version
                FROM analysis_tasks at
                LEFT JOIN task_statistics ts ON ts.task_id = at.task_id
                WHERE at.task_id = %s
                  AND at.user_id = %s
                LIMIT 1
                """,
                (int(task_id), int(user_id)),
            )
            normalized_row = cursor.fetchone()
            if not normalized_row:
                return None

            normalized_task_id = int(normalized_row["batch_id"])
            cursor.execute(
                """
                SELECT
                    document_id AS task_id,
                    task_id AS batch_id,
                    document_source_id AS id,
                    document_name AS name,
                    document_index AS `index`,
                    word_count,
                    sentence_count,
                    language,
                    DATE_FORMAT(upload_time, '%%Y-%%m-%%d %%H:%%i:%%s') AS upload_time,
                    document_content AS raw_text,
                    sentences_json
                FROM document_info
                WHERE task_id = %s
                ORDER BY document_index ASC, document_id ASC
                """,
                (normalized_task_id,),
            )
            file_rows = list(cursor.fetchall())

            batch_row = normalized_row
            response_payload = _parse_json_field(batch_row.get("response_payload_json"), {})
            response_data = response_payload if isinstance(response_payload, dict) else {}
            data = response_data.get("data")
            if not isinstance(data, dict):
                data = {}
                response_data["data"] = data

            files = []
            for row in file_rows:
                files.append(
                    {
                        "task_id": int(row["task_id"]),
                        "batch_id": int(row["batch_id"]),
                        "id": str(row["id"]),
                        "name": str(row["name"]),
                        "index": int(row["index"]),
                        "word_count": int(row.get("word_count", 0)),
                        "sentence_count": int(row.get("sentence_count", 0)),
                        "language": str(row.get("language", "")),
                        "upload_time": str(row.get("upload_time", "")),
                        "content": str(row.get("raw_text") or ""),
                        "raw_text": str(row.get("raw_text") or ""),
                        "sentences": _parse_json_field(row.get("sentences_json"), []),
                    }
                )

            normalized_topics = _fetch_normalized_task_topics(cursor, normalized_task_id)
            data["themes"] = normalized_topics
            data["doc_themes"] = normalized_topics

            data["files"] = files
            data["file_count"] = len(files)
            data["selected_task_id"] = normalized_task_id
            data["selected_file_id"] = str(files[0]["id"]) if files else ""
            data["selected_file_index"] = int(files[0]["index"]) if files else 0
            data["batch_id"] = normalized_task_id

            statistics = data.get("statistics")
            if not isinstance(statistics, dict):
                statistics = {}
                data["statistics"] = statistics
            statistics["file_count"] = len(files)
            statistics["theme_count"] = int(data.get("theme_count", batch_row.get("batch_theme_count", 0)) or 0)
            statistics["doc_theme_count"] = int(data.get("doc_theme_count", batch_row.get("batch_doc_theme_count", 0)) or 0)
            statistics["processing_time_ms"] = float(batch_row.get("processing_time_ms", 0) or 0)
            statistics["algorithm_version"] = str(batch_row.get("algorithm_version", "") or "")

            response_data["code"] = 200
            response_data["msg"] = "获取任务详情成功"
            return response_data


def delete_task_by_id(task_id: int, user_id: int) -> bool:
    """按文档任务 ID 或批次 ID 删除，验证用户所有权。"""
    ensure_database_ready()
    settings = get_db_settings()
    with get_connection(settings["database"]) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT task_id, user_id
                FROM analysis_tasks
                WHERE task_id = %s
                  AND user_id = %s
                LIMIT 1
                """,
                (int(task_id), int(user_id)),
            )
            row = cursor.fetchone()
            if not row:
                return False

            normalized_task_id = int(row["task_id"])

            # 删除批次后由外键级联清理文档、主题、关键词关系和统计。
            cursor.execute(
                "DELETE FROM analysis_tasks WHERE task_id = %s AND user_id = %s",
                (normalized_task_id, int(user_id)),
            )

            # 删除孤立关键词
            _delete_orphan_keywords(cursor)

        connection.commit()
        return True


def rename_task_topic(task_id: int, topic_identifier: str, user_id: int, new_name: str):
    """重命名当前用户任务内的主题，并同步主题 JSON 快照。"""
    ensure_database_ready()
    settings = get_db_settings()
    with get_connection(settings["database"]) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT ti.topic_id, ti.topic_record_id
                FROM topic_info ti
                INNER JOIN analysis_tasks at ON at.task_id = ti.task_id
                WHERE ti.task_id = %s
                  AND at.user_id = %s
                  AND (ti.topic_record_id = %s OR CAST(ti.topic_id AS CHAR) = %s)
                LIMIT 1
                """,
                (int(task_id), int(user_id), str(topic_identifier), str(topic_identifier)),
            )
            row = cursor.fetchone()
            if not row:
                return None
            cursor.execute(
                """
                UPDATE topic_info
                SET topic_name = %s,
                    topic_payload_json = JSON_SET(
                        COALESCE(topic_payload_json, JSON_OBJECT()),
                        '$.theme', %s
                    )
                WHERE topic_id = %s
                """,
                (str(new_name), str(new_name), int(row["topic_id"])),
            )
        connection.commit()
    return {
        "id": str(row.get("topic_record_id") or row["topic_id"]),
        "topic_id": int(row["topic_id"]),
        "theme": str(new_name),
    }


def confirm_task_topic(
    task_id: int,
    topic_identifier: str,
    user_id: int,
    confirmed: bool,
) -> dict | None:
    """持久化主题人工确认状态并记录审计。"""
    ensure_database_ready()
    settings = get_db_settings()
    with get_connection(settings["database"]) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT ti.topic_id, ti.topic_record_id
                FROM topic_info ti
                INNER JOIN analysis_tasks at ON at.task_id = ti.task_id
                WHERE ti.task_id = %s AND at.user_id = %s
                  AND (ti.topic_record_id = %s OR CAST(ti.topic_id AS CHAR) = %s)
                LIMIT 1
                """,
                (int(task_id), int(user_id), str(topic_identifier), str(topic_identifier)),
            )
            row = cursor.fetchone()
            if not row:
                return None
            cursor.execute(
                """
                UPDATE topic_info
                SET is_confirmed = %s,
                    topic_payload_json = JSON_SET(
                        COALESCE(topic_payload_json, JSON_OBJECT()),
                        '$.confirmed', %s
                    )
                WHERE topic_id = %s
                """,
                (1 if confirmed else 0, bool(confirmed), int(row["topic_id"])),
            )
            _append_task_audit(
                cursor,
                task_id,
                user_id,
                "confirm_topic" if confirmed else "unconfirm_topic",
                {"topic_id": str(topic_identifier)},
            )
        connection.commit()
    return {
        "id": str(row.get("topic_record_id") or row["topic_id"]),
        "topic_id": int(row["topic_id"]),
        "confirmed": bool(confirmed),
    }


def _refresh_task_theme_counts(cursor, task_id: int):
    """主题编辑后重新计算批次主题数量。"""
    cursor.execute("SELECT COUNT(*) AS count FROM topic_info WHERE task_id = %s", (int(task_id),))
    theme_count = int((cursor.fetchone() or {}).get("count", 0) or 0)
    cursor.execute(
        "UPDATE analysis_tasks SET theme_count = %s WHERE task_id = %s",
        (theme_count, int(task_id)),
    )
    cursor.execute(
        "UPDATE task_statistics SET theme_count = %s, doc_theme_count = %s WHERE task_id = %s",
        (theme_count, theme_count, int(task_id)),
    )


def delete_task_topic(task_id: int, topic_identifier: str, user_id: int) -> bool:
    """删除当前用户任务中的单个主题并刷新统计。"""
    ensure_database_ready()
    settings = get_db_settings()
    with get_connection(settings["database"]) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT ti.topic_id
                FROM topic_info ti
                INNER JOIN analysis_tasks at ON at.task_id = ti.task_id
                WHERE ti.task_id = %s
                  AND at.user_id = %s
                  AND (ti.topic_record_id = %s OR CAST(ti.topic_id AS CHAR) = %s)
                LIMIT 1
                """,
                (int(task_id), int(user_id), str(topic_identifier), str(topic_identifier)),
            )
            row = cursor.fetchone()
            if not row:
                return False
            cursor.execute("DELETE FROM topic_info WHERE topic_id = %s", (int(row["topic_id"]),))
            _refresh_task_theme_counts(cursor, task_id)
            _delete_orphan_keywords(cursor)
        connection.commit()
    return True


def merge_task_topics(
    task_id: int,
    topic_identifiers: list[str],
    user_id: int,
    merged_name: str,
):
    """合并同一文档中的多个主题，保留首个主题并汇总关键词关系。"""
    ensure_database_ready()
    settings = get_db_settings()
    requested_ids = [str(item) for item in topic_identifiers]
    with get_connection(settings["database"]) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT ti.topic_id, ti.topic_record_id, ti.document_id
                FROM topic_info ti
                INNER JOIN analysis_tasks at ON at.task_id = ti.task_id
                WHERE ti.task_id = %s AND at.user_id = %s
                ORDER BY ti.topic_id ASC
                """,
                (int(task_id), int(user_id)),
            )
            rows = [
                row for row in cursor.fetchall()
                if str(row.get("topic_record_id")) in requested_ids
                or str(row.get("topic_id")) in requested_ids
            ]
            if len(rows) != len(set(requested_ids)):
                return None
            if len({int(row["document_id"]) for row in rows}) != 1:
                raise ValueError("只能合并同一文档内的主题")

            target = rows[0]
            target_topic_id = int(target["topic_id"])
            source_topic_ids = [int(row["topic_id"]) for row in rows]
            placeholders = ", ".join(["%s"] * len(source_topic_ids))
            cursor.execute(
                f"""
                INSERT INTO topic_keyword_relations (
                    topic_id, keyword_id, keyword_weight, keyword_count, source_json
                )
                SELECT %s, keyword_id, MAX(keyword_weight), SUM(keyword_count), MAX(CAST(source_json AS CHAR))
                FROM topic_keyword_relations
                WHERE topic_id IN ({placeholders})
                GROUP BY keyword_id
                ON DUPLICATE KEY UPDATE
                    keyword_weight = GREATEST(keyword_weight, VALUES(keyword_weight)),
                    keyword_count = VALUES(keyword_count),
                    source_json = VALUES(source_json)
                """,
                (target_topic_id, *source_topic_ids),
            )
            removable_ids = [topic_id for topic_id in source_topic_ids if topic_id != target_topic_id]
            if removable_ids:
                removable_placeholders = ", ".join(["%s"] * len(removable_ids))
                cursor.execute(
                    f"DELETE FROM topic_info WHERE topic_id IN ({removable_placeholders})",
                    tuple(removable_ids),
                )
            cursor.execute(
                """
                UPDATE topic_info
                SET topic_name = %s,
                    topic_payload_json = JSON_SET(
                        COALESCE(topic_payload_json, JSON_OBJECT()),
                        '$.theme', %s
                    )
                WHERE topic_id = %s
                """,
                (str(merged_name), str(merged_name), target_topic_id),
            )
            _refresh_task_theme_counts(cursor, task_id)
            _delete_orphan_keywords(cursor)
        connection.commit()
    return {
        "id": str(target.get("topic_record_id") or target_topic_id),
        "topic_id": target_topic_id,
        "theme": str(merged_name),
    }


def clear_task_history(user_id: int) -> dict:
    """清空指定用户的任务列表数据。"""
    ensure_database_ready()
    settings = get_db_settings()
    with get_connection(settings["database"]) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) AS count FROM analysis_tasks WHERE user_id = %s",
                (int(user_id),),
            )
            batch_count = int((cursor.fetchone() or {}).get("count", 0))
            cursor.execute(
                "DELETE FROM analysis_tasks WHERE user_id = %s",
                (int(user_id),),
            )
            # 只清理由当前用户任务删除后留下的孤立关键词，不影响其他用户仍在使用的关键词。
            _delete_orphan_keywords(cursor)
        connection.commit()
        return {"batch_count": batch_count, "file_count": 0, "theme_count": 0}


def create_user(username: str, password_hash: str) -> dict:
    """创建系统登录用户。"""
    ensure_database_ready()
    settings = get_db_settings()
    create_time = datetime.now()
    admin_names = {
        item.strip()
        for item in os.getenv("ADMIN_USERNAMES", "admin").split(",")
        if item.strip()
    }
    is_admin = username in admin_names
    with get_connection(settings["database"]) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO users (username, password_hash, is_admin, create_time) VALUES (%s, %s, %s, %s)",
                (username, password_hash, 1 if is_admin else 0, create_time),
            )
            user_id = int(cursor.lastrowid)
        connection.commit()
        return {
            "user_id": user_id,
            "username": username,
            "is_admin": is_admin,
            "create_time": create_time.strftime("%Y-%m-%d %H:%M:%S"),
        }


def find_user_by_username(username: str) -> dict | None:
    """按用户名查询登录用户。"""
    ensure_database_ready()
    settings = get_db_settings()
    with get_connection(settings["database"]) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT user_id, username, password_hash, is_admin, create_time FROM users WHERE username = %s LIMIT 1",
                (username,),
            )
            return cursor.fetchone()


def find_user_id_by_username(username: str) -> int | None:
    """按用户名查询 user_id。"""
    ensure_database_ready()
    settings = get_db_settings()
    with get_connection(settings["database"]) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT user_id FROM users WHERE username = %s LIMIT 1",
                (username,),
            )
            row = cursor.fetchone()
            return int(row["user_id"]) if row else None
