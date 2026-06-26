import json
import logging
import os
from contextlib import contextmanager
from datetime import datetime

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
                    request_payload_json JSON NULL,
                    response_payload_json JSON NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_analysis_tasks_create_time (create_time),
                    INDEX idx_analysis_tasks_user_id (user_id),
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
            # 兼容旧库：历史库如果缺少任务状态列，启动时自动补齐。
            cursor.execute("SHOW COLUMNS FROM analysis_tasks LIKE 'task_status'")
            if cursor.fetchone() is None:
                cursor.execute(
                    "ALTER TABLE analysis_tasks ADD COLUMN task_status VARCHAR(32) NOT NULL DEFAULT '已完成' AFTER theme_count"
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

    with get_connection(settings["database"]) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO analysis_tasks (
                    task_name, file_count, theme_count, task_status,
                    create_time, user_id, request_payload_json, response_payload_json
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    task_name,
                    int(file_count),
                    0,
                    status,
                    now,
                    user_id,
                    json.dumps(request_payload or {}, ensure_ascii=False),
                    None,
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
                    response_payload_json = COALESCE(%s, response_payload_json)
                WHERE task_id = %s
                """,
                (
                    status,
                    json.dumps(response_payload, ensure_ascii=False) if response_payload is not None else None,
                    int(task_id),
                ),
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
                response_payload_json = %s
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
        cursor.execute(
            """
            INSERT INTO analysis_tasks (
                task_name, file_count, theme_count, task_status,
                create_time, user_id, request_payload_json, response_payload_json
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                task_name,
                int(statistics.get("file_count", 0)),
                int(statistics.get("theme_count", 0)),
                TASK_STATUS_DONE,
                now,
                user_id,
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
        "task_id": saved_document_ids[0] if saved_document_ids else None,
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


def find_existing_document_names(file_names: list[str]) -> list[str]:
    """检查文档名是否已被历史抽取任务使用。"""
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
                SELECT document_name AS name
                FROM document_info
                WHERE document_name IN ({placeholders})
                """,
                tuple(clean_names),
            )
            existed_names.update(str(row.get("name", "")) for row in cursor.fetchall())
    return [name for name in clean_names if name in existed_names]


def fetch_recent_files(username: str | None = None, limit: int = 20) -> list[dict]:
    """读取当前用户任务列表，包含已完成文档以及失败/分析中的批次记录。"""
    ensure_database_ready()
    settings = get_db_settings()

    user_id = None
    if username:
        user_id = find_user_id_by_username(username)

    with get_connection(settings["database"]) as connection:
        with connection.cursor() as cursor:
            base_sql = """
                SELECT
                    COALESCE(di.document_id, at.task_id) AS task_id,
                    at.task_id AS batch_id,
                    COALESCE(di.document_source_id, '') AS id,
                    COALESCE(di.document_name, at.task_name) AS name,
                    COALESCE(di.document_index, 0) AS `index`,
                    COALESCE(di.word_count, 0) AS word_count,
                    COALESCE(di.sentence_count, 0) AS sentence_count,
                    COALESCE(di.language, '') AS language,
                    DATE_FORMAT(COALESCE(di.upload_time, at.create_time), '%%Y-%%m-%%d %%H:%%i:%%s') AS upload_time,
                    at.task_name,
                    at.file_count AS doc_count,
                    at.task_status AS status,
                    at.task_status
                FROM analysis_tasks at
                LEFT JOIN document_info di ON di.task_id = at.task_id
            """
            if user_id:
                cursor.execute(
                    base_sql
                    + """
                    WHERE at.user_id = %s
                    ORDER BY COALESCE(di.upload_time, at.create_time) DESC, at.task_id DESC, di.document_id DESC
                    LIMIT %s
                    """,
                    (user_id, int(limit)),
                )
            else:
                cursor.execute(
                    base_sql
                    + """
                    ORDER BY COALESCE(di.upload_time, at.create_time) DESC, at.task_id DESC, di.document_id DESC
                    LIMIT %s
                    """,
                    (int(limit),),
                )
            return list(cursor.fetchall())


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


def _delete_orphan_keywords(cursor):
    """删除不再被任何主题引用的关键词。"""
    cursor.execute(
        """
        DELETE ki FROM keyword_info ki
        LEFT JOIN topic_keyword_relations tkr ON tkr.keyword_id = ki.keyword_id
        WHERE tkr.keyword_id IS NULL
        """
    )


def fetch_task_detail(task_id: int) -> dict | None:
    """按任务主键读取整批分析结果。"""
    ensure_database_ready()
    settings = get_db_settings()
    with get_connection(settings["database"]) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    di.document_id AS task_id,
                    di.task_id AS batch_id,
                    di.document_source_id AS file_id,
                    di.document_index AS file_index,
                    at.file_count AS batch_file_count,
                    at.theme_count AS batch_theme_count,
                    at.task_status,
                    at.response_payload_json,
                    ts.doc_theme_count AS batch_doc_theme_count,
                    ts.processing_time_ms,
                    ts.algorithm_version
                FROM document_info di
                INNER JOIN analysis_tasks at ON at.task_id = di.task_id
                LEFT JOIN task_statistics ts ON ts.task_id = at.task_id
                WHERE di.document_id = %s
                LIMIT 1
                """,
                (int(task_id),),
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

            data["files"] = files
            data["file_count"] = len(files)
            data["selected_task_id"] = int(batch_row["task_id"])
            data["selected_file_id"] = str(batch_row["file_id"])
            data["selected_file_index"] = int(batch_row["file_index"])
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


def delete_task_by_id(task_id: int, username: str | None = None) -> bool:
    """按文档任务 ID 或批次 ID 删除，验证用户所有权。"""
    ensure_database_ready()
    settings = get_db_settings()
    with get_connection(settings["database"]) as connection:
        with connection.cursor() as cursor:
            # 先按文档 ID 查批次，失败/分析中的任务可能还没有文档行。
            cursor.execute(
                """
                SELECT at.task_id, at.user_id
                FROM document_info di
                INNER JOIN analysis_tasks at ON at.task_id = di.task_id
                WHERE di.document_id = %s
                LIMIT 1
                """,
                (int(task_id),),
            )
            row = cursor.fetchone()
            if not row:
                cursor.execute(
                    """
                    SELECT task_id, user_id
                    FROM analysis_tasks
                    WHERE task_id = %s
                    LIMIT 1
                    """,
                    (int(task_id),),
                )
                row = cursor.fetchone()
            if not row:
                return False

            if username:
                uid = find_user_id_by_username(username)
                task_uid = int(row.get("user_id") or 0)
                if not uid or task_uid != uid:
                    return False

            normalized_task_id = int(row["task_id"])

            # 级联删除：document_info 的 FK ON DELETE CASCADE 会清理 topic_info
            cursor.execute("DELETE FROM document_info WHERE task_id = %s", (normalized_task_id,))

            # 清理分析任务和统计
            cursor.execute("DELETE FROM analysis_tasks WHERE task_id = %s", (normalized_task_id,))

            # 删除孤立关键词
            _delete_orphan_keywords(cursor)

        connection.commit()
        return True


def clear_task_history(username: str | None = None) -> dict:
    """清空指定用户的任务列表数据。"""
    ensure_database_ready()
    settings = get_db_settings()
    with get_connection(settings["database"]) as connection:
        with connection.cursor() as cursor:
            if username:
                uid = find_user_id_by_username(username)
                if not uid:
                    return {"batch_count": 0, "file_count": 0, "theme_count": 0}
                cursor.execute("SELECT COUNT(*) AS count FROM analysis_tasks WHERE user_id = %s", (uid,))
                batch_count = int((cursor.fetchone() or {}).get("count", 0))
                cursor.execute("DELETE FROM analysis_tasks WHERE user_id = %s", (uid,))
            else:
                cursor.execute("SELECT COUNT(*) AS count FROM analysis_tasks")
                batch_count = int((cursor.fetchone() or {}).get("count", 0))
                cursor.execute("DELETE FROM analysis_tasks")
            cursor.execute("DELETE FROM keyword_info")
        connection.commit()
        return {"batch_count": batch_count, "file_count": 0, "theme_count": 0}


def create_user(username: str, password_hash: str) -> dict:
    """创建系统登录用户。"""
    ensure_database_ready()
    settings = get_db_settings()
    create_time = datetime.now()
    with get_connection(settings["database"]) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO users (username, password_hash, create_time) VALUES (%s, %s, %s)",
                (username, password_hash, create_time),
            )
            user_id = int(cursor.lastrowid)
        connection.commit()
        return {
            "user_id": user_id,
            "username": username,
            "create_time": create_time.strftime("%Y-%m-%d %H:%M:%S"),
        }


def find_user_by_username(username: str) -> dict | None:
    """按用户名查询登录用户。"""
    ensure_database_ready()
    settings = get_db_settings()
    with get_connection(settings["database"]) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT user_id, username, password_hash, create_time FROM users WHERE username = %s LIMIT 1",
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
