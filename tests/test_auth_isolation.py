import importlib
import sys
import types
import unittest
from unittest.mock import Mock
from werkzeug.security import generate_password_hash


def _install_dependency_fakes():
    """隔离 NLP 与数据库依赖，只通过 Flask HTTP interface 验证认证行为。"""
    app_logic = types.ModuleType("app_logic")
    app_logic.build_extract_result = Mock()
    app_logic.build_wordcloud_data = Mock()
    app_logic.parse_extract_params = Mock()
    app_logic.parse_extract_texts = Mock()
    app_logic.parse_request_file_names = Mock()
    app_logic.parse_wordcloud_params = Mock()
    app_logic.parse_wordcloud_texts = Mock()

    database = types.ModuleType("database")
    database.TASK_STATUS_ERROR = "失败"
    database.TASK_STATUS_DONE = "已完成"
    for name in (
        "clear_task_history",
        "create_analysis_task_record",
        "create_user",
        "delete_task_by_id",
        "fetch_task_detail",
        "fetch_task_summary",
        "query_task_page",
        "rename_task_topic",
        "delete_task_topic",
        "merge_task_topics",
        "update_task_metadata",
        "batch_update_tasks",
        "copy_task",
        "get_task_retry_payload",
        "fetch_task_audit",
        "fetch_admin_statistics",
        "confirm_task_topic",
        "fetch_task_comparison_snapshots",
        "create_task_share",
        "fetch_shared_task",
        "save_task_filter",
        "list_task_filters",
        "delete_task_filter",
        "find_user_by_username",
        "find_user_id_by_username",
        "find_existing_document_names",
        "find_task_by_request_id",
        "init_database",
        "save_extract_result",
        "save_wordcloud_result",
        "update_analysis_task_status",
        "update_analysis_task_progress",
    ):
        setattr(database, name, Mock())

    database.fetch_task_summary.return_value = {
        "total_count": 0,
        "done_count": 0,
        "running_count": 0,
        "error_count": 0,
        "document_count": 0,
    }
    database.query_task_page.return_value = {
        "items": [],
        "pagination": {"page": 1, "page_size": 20, "total": 0, "total_pages": 1},
        "focus_page": None,
    }

    sys.modules["app_logic"] = app_logic
    sys.modules["database"] = database
    return database


class AuthIsolationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.database = _install_dependency_fakes()
        cls.app_logic = sys.modules["app_logic"]
        sys.modules.pop("api_adapter", None)
        cls.module = importlib.import_module("api_adapter")
        cls.module.app.config.update(TESTING=True, SECRET_KEY="test-secret")

    def setUp(self):
        for value in vars(self.database).values():
            if isinstance(value, Mock):
                value.reset_mock()
        for value in vars(self.app_logic).values():
            if isinstance(value, Mock):
                value.reset_mock()
        self.client = self.module.app.test_client()

    def test_unauthenticated_user_cannot_list_tasks(self):
        response = self.client.get("/task")

        self.assertEqual(401, response.status_code)
        self.assertEqual(401, response.get_json()["code"])
        self.database.query_task_page.assert_not_called()

    def test_logged_in_user_only_lists_own_tasks(self):
        self.database.find_user_by_username.return_value = {
            "user_id": 7,
            "username": "alice",
            "password_hash": generate_password_hash("secret12"),
        }

        login_response = self.client.post(
            "/api/auth/login",
            json={"username": "alice", "password": "secret12"},
        )
        list_response = self.client.get("/task")

        self.assertEqual(200, login_response.status_code)
        self.assertEqual(200, list_response.status_code)
        self.database.query_task_page.assert_called_once_with(
            7,
            page=1,
            page_size=20,
            keyword="",
            status="all",
            days=0,
            sort_order="newest",
            focus_task_id=None,
            archived="active",
        )

    def test_task_list_supports_server_side_filters_and_pagination(self):
        """任务页参数由接口校验后原样交给当前用户范围的查询模块。"""
        self.database.find_user_by_username.return_value = {
            "user_id": 7,
            "username": "alice",
            "password_hash": generate_password_hash("secret12"),
        }
        self.database.query_task_page.return_value = {
            "items": [{"task_id": 42, "name": "能源报告"}],
            "pagination": {"page": 2, "page_size": 10, "total": 11, "total_pages": 2},
            "focus_page": 2,
        }
        self.client.post(
            "/api/auth/login",
            json={"username": "alice", "password": "secret12"},
        )

        response = self.client.get(
            "/task?page=2&page_size=10&keyword=%E8%83%BD%E6%BA%90&status=done"
            "&days=30&sort=oldest&focus_task_id=42"
        )

        self.assertEqual(200, response.status_code)
        self.assertEqual(42, response.get_json()["data"][0]["task_id"])
        self.assertEqual(11, response.get_json()["pagination"]["total"])
        self.database.query_task_page.assert_called_once_with(
            7,
            page=2,
            page_size=10,
            keyword="能源",
            status="done",
            days=30,
            sort_order="oldest",
            focus_task_id=42,
            archived="active",
        )

    def test_clear_tasks_only_clears_current_user(self):
        self.database.find_user_by_username.return_value = {
            "user_id": 7,
            "username": "alice",
            "password_hash": generate_password_hash("secret12"),
        }
        self.database.clear_task_history.return_value = {
            "batch_count": 2,
            "file_count": 3,
            "theme_count": 8,
        }
        self.client.post(
            "/api/auth/login",
            json={"username": "alice", "password": "secret12"},
        )

        response = self.client.delete("/task")

        self.assertEqual(200, response.status_code)
        self.database.clear_task_history.assert_called_once_with(7)

    def test_task_detail_is_scoped_to_current_user(self):
        self.database.find_user_by_username.return_value = {
            "user_id": 7,
            "username": "alice",
            "password_hash": generate_password_hash("secret12"),
        }
        self.database.fetch_task_detail.return_value = {
            "code": 200,
            "msg": "获取成功",
            "data": {"files": []},
        }
        self.client.post(
            "/api/auth/login",
            json={"username": "alice", "password": "secret12"},
        )

        response = self.client.get("/task/42")

        self.assertEqual(200, response.status_code)
        self.database.fetch_task_detail.assert_called_once_with(42, 7)

    def test_delete_task_is_scoped_to_current_user(self):
        self.database.find_user_by_username.return_value = {
            "user_id": 7,
            "username": "alice",
            "password_hash": generate_password_hash("secret12"),
        }
        self.database.delete_task_by_id.return_value = True
        self.client.post(
            "/api/auth/login",
            json={"username": "alice", "password": "secret12"},
        )

        response = self.client.delete("/task/42")

        self.assertEqual(200, response.status_code)
        self.database.delete_task_by_id.assert_called_once_with(42, 7)

    def test_user_can_update_owned_task_metadata(self):
        """任务名称、标签和归档状态只能由任务所有者修改。"""
        self.database.find_user_by_username.return_value = {
            "user_id": 7,
            "username": "alice",
            "password_hash": generate_password_hash("secret12"),
        }
        self.database.update_task_metadata.return_value = {
            "task_id": 42,
            "name": "季度能源报告",
            "tags": ["能源", "季度"],
            "archived": True,
        }
        self.client.post(
            "/api/auth/login",
            json={"username": "alice", "password": "secret12"},
        )

        response = self.client.patch(
            "/task/42",
            json={"name": "季度能源报告", "tags": ["能源", "季度"], "archived": True},
        )

        self.assertEqual(200, response.status_code)
        self.assertTrue(response.get_json()["data"]["archived"])
        self.database.update_task_metadata.assert_called_once_with(
            42,
            7,
            name="季度能源报告",
            tags=["能源", "季度"],
            archived=True,
        )

    def test_user_can_batch_archive_owned_tasks(self):
        """批量操作仍由后端按当前用户收口，客户端不能越权指定用户。"""
        self.database.find_user_by_username.return_value = {
            "user_id": 7,
            "username": "alice",
            "password_hash": generate_password_hash("secret12"),
        }
        self.database.batch_update_tasks.return_value = {
            "action": "archive",
            "affected_count": 2,
            "task_ids": [11, 12],
        }
        self.client.post(
            "/api/auth/login",
            json={"username": "alice", "password": "secret12"},
        )

        response = self.client.post(
            "/task/batch",
            json={"action": "archive", "task_ids": [11, 12]},
        )

        self.assertEqual(200, response.status_code)
        self.assertEqual(2, response.get_json()["data"]["affected_count"])
        self.database.batch_update_tasks.assert_called_once_with(
            7,
            [11, 12],
            "archive",
            tags=None,
        )

    def test_user_can_copy_owned_task(self):
        """复制任务生成新的批次 ID，并保留来源任务关系。"""
        self.database.find_user_by_username.return_value = {
            "user_id": 7,
            "username": "alice",
            "password_hash": generate_password_hash("secret12"),
        }
        self.database.copy_task.return_value = {
            "task_id": 99,
            "parent_task_id": 42,
            "name": "能源报告 - 副本",
        }
        self.client.post(
            "/api/auth/login",
            json={"username": "alice", "password": "secret12"},
        )

        response = self.client.post("/task/42/copy")

        self.assertEqual(201, response.status_code)
        self.assertEqual(99, response.get_json()["data"]["task_id"])
        self.database.copy_task.assert_called_once_with(42, 7)

    def test_failed_task_can_prepare_retry_payload(self):
        """失败任务重跑只返回当前用户原始请求，并强制生成新的幂等键。"""
        self.database.find_user_by_username.return_value = {
            "user_id": 7,
            "username": "alice",
            "password_hash": generate_password_hash("secret12"),
        }
        self.database.get_task_retry_payload.return_value = {
            "texts": ["能源分析正文"],
            "file_names": ["能源.txt"],
            "record_recent": True,
            "_retry_source_task_id": 42,
        }
        self.client.post(
            "/api/auth/login",
            json={"username": "alice", "password": "secret12"},
        )

        response = self.client.post("/task/42/retry")

        self.assertEqual(200, response.status_code)
        self.assertNotIn("request_id", response.get_json()["data"]["request"])
        self.database.get_task_retry_payload.assert_called_once_with(42, 7)

    def test_admin_can_read_aggregate_statistics(self):
        """管理员统计只通过只读接口返回聚合值。"""
        self.database.find_user_by_username.return_value = {
            "user_id": 1,
            "username": "admin",
            "password_hash": generate_password_hash("secret12"),
            "is_admin": 1,
        }
        self.database.fetch_admin_statistics.return_value = {
            "user_count": 3,
            "task_count": 12,
            "document_count": 20,
        }
        self.client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "secret12"},
        )

        response = self.client.get("/api/admin/statistics")

        self.assertEqual(200, response.status_code)
        self.assertEqual(12, response.get_json()["data"]["task_count"])
        self.database.fetch_admin_statistics.assert_called_once_with()

    def test_user_can_create_read_only_share_for_owned_task(self):
        """创建分享令牌时后端始终绑定当前用户和指定任务。"""
        self.database.find_user_by_username.return_value = {
            "user_id": 7,
            "username": "alice",
            "password_hash": generate_password_hash("secret12"),
        }
        self.database.create_task_share.return_value = {
            "token": "safe-share-token",
            "expires_at": "2026-07-23 10:00:00",
        }
        self.client.post(
            "/api/auth/login",
            json={"username": "alice", "password": "secret12"},
        )

        response = self.client.post("/task/42/share", json={"expires_days": 7})

        self.assertEqual(201, response.status_code)
        self.assertEqual("safe-share-token", response.get_json()["data"]["token"])
        self.database.create_task_share.assert_called_once_with(42, 7, 7)

    def test_user_can_rename_topic_in_owned_task(self):
        self.database.find_user_by_username.return_value = {
            "user_id": 7,
            "username": "alice",
            "password_hash": generate_password_hash("secret12"),
        }
        self.database.rename_task_topic.return_value = {"id": "topic-1", "theme": "新能源主题"}
        self.client.post(
            "/api/auth/login",
            json={"username": "alice", "password": "secret12"},
        )

        response = self.client.patch(
            "/task/11/topics/topic-1",
            json={"name": "新能源主题"},
        )

        self.assertEqual(200, response.status_code)
        self.assertEqual("新能源主题", response.get_json()["data"]["theme"])
        self.database.rename_task_topic.assert_called_once_with(11, "topic-1", 7, "新能源主题")

    def test_user_can_confirm_topic_in_owned_task(self):
        """人工确认状态持久化到主题记录，详情页可稳定恢复。"""
        self.database.find_user_by_username.return_value = {
            "user_id": 7,
            "username": "alice",
            "password_hash": generate_password_hash("secret12"),
        }
        self.database.confirm_task_topic.return_value = {"id": "topic-1", "confirmed": True}
        self.client.post(
            "/api/auth/login",
            json={"username": "alice", "password": "secret12"},
        )

        response = self.client.patch(
            "/task/11/topics/topic-1/confirmation",
            json={"confirmed": True},
        )

        self.assertEqual(200, response.status_code)
        self.assertTrue(response.get_json()["data"]["confirmed"])
        self.database.confirm_task_topic.assert_called_once_with(11, "topic-1", 7, True)

    def test_user_can_delete_topic_from_owned_task(self):
        self.database.find_user_by_username.return_value = {
            "user_id": 7,
            "username": "alice",
            "password_hash": generate_password_hash("secret12"),
        }
        self.database.delete_task_topic.return_value = True
        self.client.post(
            "/api/auth/login",
            json={"username": "alice", "password": "secret12"},
        )

        response = self.client.delete("/task/11/topics/topic-1")

        self.assertEqual(200, response.status_code)
        self.database.delete_task_topic.assert_called_once_with(11, "topic-1", 7)

    def test_user_can_merge_topics_in_owned_task(self):
        self.database.find_user_by_username.return_value = {
            "user_id": 7,
            "username": "alice",
            "password_hash": generate_password_hash("secret12"),
        }
        self.database.merge_task_topics.return_value = {"id": "topic-1", "theme": "综合能源"}
        self.client.post(
            "/api/auth/login",
            json={"username": "alice", "password": "secret12"},
        )

        response = self.client.post(
            "/task/11/topics/merge",
            json={"topic_ids": ["topic-1", "topic-2"], "name": "综合能源"},
        )

        self.assertEqual(200, response.status_code)
        self.assertEqual("综合能源", response.get_json()["data"]["theme"])
        self.database.merge_task_topics.assert_called_once_with(
            11, ["topic-1", "topic-2"], 7, "综合能源"
        )

    def test_unauthenticated_user_cannot_extract_documents(self):
        response = self.client.post("/extract", json={"text": "测试文档内容"})

        self.assertEqual(401, response.status_code)
        self.assertEqual(401, response.get_json()["code"])

    def test_logout_invalidates_server_session(self):
        self.database.find_user_by_username.return_value = {
            "user_id": 7,
            "username": "alice",
            "password_hash": generate_password_hash("secret12"),
        }
        self.client.post(
            "/api/auth/login",
            json={"username": "alice", "password": "secret12"},
        )

        me_response = self.client.get("/api/auth/me")
        logout_response = self.client.post("/api/auth/logout")
        list_response = self.client.get("/task")

        self.assertEqual(200, me_response.status_code)
        self.assertEqual("alice", me_response.get_json()["data"]["username"])
        self.assertEqual(200, logout_response.status_code)
        self.assertEqual(401, list_response.status_code)

    def test_extract_ignores_client_username_and_uses_session_user(self):
        self.database.find_user_by_username.return_value = {
            "user_id": 7,
            "username": "alice",
            "password_hash": generate_password_hash("secret12"),
        }
        self.database.create_analysis_task_record.return_value = 11
        self.database.save_extract_result.return_value = {"task_id": 21, "batch_id": 11}
        self.app_logic.parse_extract_texts.return_value = (["测试文档内容"], None)
        self.app_logic.parse_extract_params.return_value = ({
            "return_topics": True,
            "return_matrix": True,
            "debug": False,
        }, None)
        self.app_logic.parse_request_file_names.return_value = (["测试.txt"], None)
        self.app_logic.build_extract_result.return_value = {
            "files": [{"id": "doc1", "name": "测试.txt"}],
            "doc_themes": [],
            "topics": [],
            "matrix": {"values": []},
            "relation": {},
            "heatmap": {},
            "statistics": {"theme_count": 0, "doc_theme_count": 0},
            "debug": {"modeled_topic_k": 0, "unit_count": 0},
        }
        self.client.post(
            "/api/auth/login",
            json={"username": "alice", "password": "secret12"},
        )

        response = self.client.post(
            "/extract",
            json={
                "text": "测试文档内容",
                "username": "mallory",
                "record_recent": True,
            },
        )

        self.assertEqual(200, response.status_code)
        self.assertEqual(11, response.get_json()["data"]["task_id"])
        request_payload = self.database.create_analysis_task_record.call_args.args[0]
        self.assertNotIn("username", request_payload)
        self.assertEqual(
            7,
            self.database.create_analysis_task_record.call_args.kwargs["user_id"],
        )
        self.assertEqual(7, self.database.save_extract_result.call_args.kwargs["user_id"])

    def test_extract_replays_completed_idempotent_request_without_creating_batch(self):
        """同一用户重复提交幂等键时直接复用已完成结果。"""
        self.database.find_user_by_username.return_value = {
            "user_id": 7,
            "username": "alice",
            "password_hash": generate_password_hash("secret12"),
        }
        self.database.find_task_by_request_id.return_value = {
            "task_id": 11,
            "task_status": "已完成",
            "response_payload": {"code": 200, "msg": "成功", "data": {"themes": []}},
        }
        self.client.post(
            "/api/auth/login",
            json={"username": "alice", "password": "secret12"},
        )

        response = self.client.post(
            "/extract",
            json={"text": "测试文档内容", "request_id": "request-001", "record_recent": True},
        )

        self.assertEqual(200, response.status_code)
        self.assertEqual(11, response.get_json()["data"]["task_id"])
        self.database.find_task_by_request_id.assert_called_once_with(7, "request-001")
        self.database.create_analysis_task_record.assert_not_called()
        self.app_logic.build_extract_result.assert_not_called()


if __name__ == "__main__":
    unittest.main()
