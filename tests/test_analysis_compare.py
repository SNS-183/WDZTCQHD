import unittest

from analysis_compare import compare_task_snapshots


class AnalysisCompareTest(unittest.TestCase):
    def test_compare_reports_common_and_unique_themes(self):
        snapshots = [
            {
                "task_id": 1,
                "name": "第一版",
                "algorithm_version": "http-v1.1.0",
                "themes": [
                    {"theme": "新能源", "keywords": ["储能", "光伏"]},
                    {"theme": "市场", "keywords": ["价格"]},
                ],
            },
            {
                "task_id": 2,
                "name": "第二版",
                "algorithm_version": "http-v1.2.0",
                "themes": [
                    {"theme": "新能源", "keywords": ["储能", "电网"]},
                    {"theme": "政策", "keywords": ["补贴"]},
                ],
            },
        ]

        result = compare_task_snapshots(snapshots)

        self.assertEqual(["新能源"], result["common_themes"])
        self.assertEqual(["市场"], result["tasks"][0]["unique_themes"])
        self.assertEqual(["政策"], result["tasks"][1]["unique_themes"])
        self.assertEqual(0.2, result["keyword_jaccard"])


if __name__ == "__main__":
    unittest.main()
