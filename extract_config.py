"""主题抽取配置的集中解析与校验模块。"""


DEFAULT_EXTRACT_CONFIG = {
    "topic_k": 5,
    "topn_keywords": 10,
    "granularity": "sentence",
    "with_evidence": True,
    "evidence_topn": 30,
    "return_topics": True,
    "return_matrix": True,
    "normalize_score": True,
    "debug": False,
    "custom_stopwords": [],
    "domain_terms": [],
}


def _normalize_term_list(value, field_name: str):
    if not isinstance(value, list):
        return None, f"参数错误：{field_name} 必须为数组"
    terms = []
    for item in value:
        term = str(item or "").strip()
        if term and term not in terms:
            terms.append(term)
    if len(terms) > 200 or any(len(term) > 30 for term in terms):
        return None, f"参数错误：{field_name} 最多 200 项且单项不超过 30 字"
    return terms, None


def parse_extract_params(request_data: dict):
    """解析 /extract 参数，保持默认值稳定并集中返回中文校验错误。"""
    params = dict(DEFAULT_EXTRACT_CONFIG)
    for key in DEFAULT_EXTRACT_CONFIG:
        if key in request_data:
            params[key] = request_data.get(key)

    try:
        params["topic_k"] = int(params["topic_k"])
        params["topn_keywords"] = int(params["topn_keywords"])
        params["evidence_topn"] = int(params["evidence_topn"])
    except Exception:
        return None, "参数错误：topic_k/topn_keywords/evidence_topn 必须为整数"

    bool_fields = ["with_evidence", "return_topics", "return_matrix", "normalize_score", "debug"]
    for field in bool_fields:
        if not isinstance(params[field], bool):
            return None, f"参数错误：{field} 必须为布尔值"

    if params["granularity"] not in {"sentence", "doc"}:
        return None, "参数错误：granularity 仅支持 sentence 或 doc"
    if not (1 <= params["topic_k"] <= 30):
        return None, "参数错误：topic_k 需在 1~30 之间"
    if not (3 <= params["topn_keywords"] <= 50):
        return None, "参数错误：topn_keywords 需在 3~50 之间"
    if not (1 <= params["evidence_topn"] <= 200):
        return None, "参数错误：evidence_topn 需在 1~200 之间"

    for field in ("custom_stopwords", "domain_terms"):
        terms, error = _normalize_term_list(params[field], field)
        if error:
            return None, error
        params[field] = terms
    return params, None
