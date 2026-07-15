import base64
import sys
import types
import unittest
from unittest.mock import patch

# 上传边界测试不执行 NLP，隔离本机二进制科学计算依赖。
numpy_module = types.ModuleType("numpy")
jieba_module = types.ModuleType("jieba")
jieba_posseg_module = types.ModuleType("jieba.posseg")
jieba_module.posseg = jieba_posseg_module
sklearn_module = types.ModuleType("sklearn")
sklearn_cluster_module = types.ModuleType("sklearn.cluster")
sklearn_cluster_module.KMeans = object
sklearn_feature_module = types.ModuleType("sklearn.feature_extraction")
sklearn_text_module = types.ModuleType("sklearn.feature_extraction.text")
sklearn_text_module.TfidfVectorizer = object
sys.modules.update({
    "numpy": numpy_module,
    "jieba": jieba_module,
    "jieba.posseg": jieba_posseg_module,
    "sklearn": sklearn_module,
    "sklearn.cluster": sklearn_cluster_module,
    "sklearn.feature_extraction": sklearn_feature_module,
    "sklearn.feature_extraction.text": sklearn_text_module,
})

import app_logic


class UploadLimitsTest(unittest.TestCase):
    def test_rejects_too_many_files_before_parsing(self):
        payload = {"files": [{"name": f"{index}.txt", "text": "有效正文内容"} for index in range(3)]}

        with patch.object(app_logic, "MAX_EXTRACT_FILE_COUNT", 2):
            texts, error = app_logic.parse_extract_texts(payload)

        self.assertIsNone(texts)
        self.assertIn("文件数量不能超过 2", error)

    def test_rejects_single_binary_file_over_size_limit(self):
        payload = {
            "name": "large.pdf",
            "content_base64": base64.b64encode(b"12345").decode("ascii"),
        }

        with patch.object(app_logic, "MAX_SINGLE_FILE_BYTES", 4):
            with self.assertRaisesRegex(ValueError, "文件大小不能超过"):
                app_logic.extract_text_from_document_payload(payload)

    def test_rejects_total_text_over_limit(self):
        payload = {"texts": ["1234", "5678"]}

        with patch.object(app_logic, "MAX_TOTAL_TEXT_CHARS", 7):
            texts, error = app_logic.parse_extract_texts(payload)

        self.assertIsNone(texts)
        self.assertIn("文本总长度不能超过 7", error)


if __name__ == "__main__":
    unittest.main()
