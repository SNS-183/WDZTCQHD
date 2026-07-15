import unittest
from unittest.mock import patch

import database


class FakeCursor:
    def __init__(self, fetchone_values=None, fetchall_value=None):
        self.calls = []
        self.fetchone_values = list(fetchone_values or [])
        self.fetchall_value = list(fetchall_value or [])

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def execute(self, query, params=None):
        self.calls.append((" ".join(str(query).split()), params))

    def fetchone(self):
        return self.fetchone_values.pop(0) if self.fetchone_values else None

    def fetchall(self):
        return self.fetchall_value


class FakeConnection:
    def __init__(self, cursor):
        self.cursor_instance = cursor
        self.committed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def cursor(self):
        return self.cursor_instance

    def commit(self):
        self.committed = True


class DatabaseIsolationTest(unittest.TestCase):
    def run_with_connection(self, connection, callback):
        with (
            patch.object(database, "ensure_database_ready"),
            patch.object(database, "get_db_settings", return_value={"database": "test"}),
            patch.object(database, "get_connection", return_value=connection),
        ):
            return callback()

    def test_task_list_query_always_filters_current_user(self):
        cursor = FakeCursor(fetchall_value=[])
        connection = FakeConnection(cursor)

        result = self.run_with_connection(
            connection,
            lambda: database.fetch_recent_files(7, 20),
        )

        self.assertEqual([], result)
        query, params = cursor.calls[-1]
        self.assertIn("WHERE at.user_id = %s", query)
        self.assertEqual((7, 20), params)

    def test_task_detail_query_checks_owner(self):
        cursor = FakeCursor(fetchone_values=[None])
        connection = FakeConnection(cursor)

        result = self.run_with_connection(
            connection,
            lambda: database.fetch_task_detail(42, 7),
        )

        self.assertIsNone(result)
        query, params = cursor.calls[0]
        self.assertIn("AND at.user_id = %s", query)
        self.assertEqual((42, 7), params)

    def test_clear_history_never_executes_global_task_delete(self):
        cursor = FakeCursor(fetchone_values=[{"count": 2}])
        connection = FakeConnection(cursor)

        result = self.run_with_connection(
            connection,
            lambda: database.clear_task_history(7),
        )

        self.assertEqual(2, result["batch_count"])
        self.assertTrue(connection.committed)
        task_delete_calls = [
            (query, params)
            for query, params in cursor.calls
            if query.startswith("DELETE FROM analysis_tasks")
        ]
        self.assertEqual(
            [("DELETE FROM analysis_tasks WHERE user_id = %s", (7,))],
            task_delete_calls,
        )
        self.assertFalse(any(query == "DELETE FROM keyword_info" for query, _ in cursor.calls))


if __name__ == "__main__":
    unittest.main()
