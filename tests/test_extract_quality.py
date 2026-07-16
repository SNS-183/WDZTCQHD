import unittest

from extract_config import parse_extract_params


class ExtractQualityTest(unittest.TestCase):
    def test_extract_params_accept_custom_dictionary_configuration(self):
        """自定义停用词和领域词通过公开参数 interface 进入抽取模块。"""
        params, error = parse_extract_params({
            "topic_k": 4,
            "topn_keywords": 8,
            "granularity": "doc",
            "custom_stopwords": ["研究", "分析"],
            "domain_terms": ["新能源", "储能系统"],
        })

        self.assertIsNone(error)
        self.assertEqual(["研究", "分析"], params["custom_stopwords"])
        self.assertEqual(["新能源", "储能系统"], params["domain_terms"])


if __name__ == "__main__":
    unittest.main()
