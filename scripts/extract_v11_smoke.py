import json
import sys
from pathlib import Path

# 允许从 scripts 目录直接运行，确保可以导入项目根目录模块
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from api_adapter import app


def run_smoke_test():
    # 最小自测：3 条短文本 + topic_k=5
    payload = {
        "texts": [
            "社区早餐工程让上班族更方便，便民服务持续优化。",
            "城市治理强调数字监管与协同机制，提升公共服务效率。",
            "民生保障与基层服务体系建设持续推进，关注居民需求。"
        ],
        "topic_k": 5,
        "topn_keywords": 10,
        "granularity": "sentence",
        "with_evidence": True,
        "evidence_topn": 30,
        "return_topics": True,
        "return_matrix": True,
        "normalize_score": True,
        "debug": False,
    }

    with app.test_client() as client:
        resp = client.post("/extract", json=payload)
        data = resp.get_json()

    assert resp.status_code == 200, f"状态码异常: {resp.status_code}, body={data}"
    assert isinstance(data, dict), "响应不是 JSON 对象"

    content = data.get("data", {})
    themes = content.get("themes", [])
    files = content.get("files", [])
    topics = content.get("topics", [])
    matrix = content.get("matrix", {})

    file_count = len(files)
    topic_k = payload["topic_k"]

    # a) themes 数量约等于 file_count * topic_k（此实现为严格相等）
    assert len(themes) == file_count * topic_k, (
        f"themes 数量不正确: {len(themes)} != {file_count} * {topic_k}"
    )

    # b) topic_index 覆盖 1..K
    topic_indexes = sorted({int(t.get("topic_index", 0)) for t in themes})
    assert topic_indexes == list(range(1, topic_k + 1)), (
        f"topic_index 覆盖不完整: {topic_indexes}"
    )

    # c) matrix.values 维度正确（len(y)=file_count, len(x)=K）
    x = matrix.get("x", [])
    y = matrix.get("y", [])
    values = matrix.get("values", [])
    assert len(x) == topic_k, f"matrix.x 长度异常: {len(x)} != {topic_k}"
    assert len(y) == file_count, f"matrix.y 长度异常: {len(y)} != {file_count}"
    assert len(values) == file_count, f"matrix.values 行数异常: {len(values)} != {file_count}"
    assert all(len(row) == topic_k for row in values), "matrix.values 列数异常"

    # d) JSON 可序列化且关键字段齐全
    json.dumps(data, ensure_ascii=False)
    assert "statistics" in content, "缺少 statistics"
    assert "themes" in content, "缺少 themes"
    assert "files" in content, "缺少 files"
    assert len(topics) == topic_k, f"topics 长度异常: {len(topics)} != {topic_k}"

    print("SMOKE_TEST_OK")
    print(f"file_count={file_count}, topic_k={topic_k}, themes={len(themes)}")


if __name__ == "__main__":
    run_smoke_test()
