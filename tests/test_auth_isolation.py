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
        "fetch_recent_files",
        "fetch_task_summary",
        "find_user_by_username",
        "find_user_id_by_username",
        "find_existing_document_names",
        "find_task_by_request_id",
        "init_database",
        "save_extract_result",
        "save_wordcloud_result",
        "update_analysis_task_status",
    ):
        setattr(database, name, Mock())

    database.fetch_task_summary.return_value = {
        "total_count": 0,
        "done_count": 0,
        "running_count": 0,
        "error_count": 0,
        "document_count": 0,
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
        self.database.fetch_recent_files.assert_not_called()

    def test_logged_in_user_only_lists_own_tasks(self):
        self.database.find_user_by_username.return_value = {
            "user_id": 7,
            "username": "alice",
            "password_hash": generate_password_hash("secret12"),
        }
        self.database.fetch_recent_files.return_value = []

        login_response = self.client.post(
            "/api/auth/login",
            json={"username": "alice", "password": "secret12"},
        )
        list_response = self.client.get("/task")

        self.assertEqual(200, login_response.status_code)
        self.assertEqual(200, list_response.status_code)
        self.database.fetch_recent_files.assert_called_once_with(7, 20)

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
