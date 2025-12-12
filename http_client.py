import requests


def pretty_print_single_response(data: dict):
    """打印后端返回的结构，方便你调试。"""
    code = data.get("code")
    msg = data.get("msg")
    print(f"code: {code} | msg: {msg}")
    print("=" * 60)

    # 取 data 节点
    d = data.get("data") or {}

    # 1）打印文件信息
    files = d.get("files", [])
    if files:
        print("【文件信息】")
        for f in files:
            print(
                f"- 文件ID: {f.get('id')} | 名称: {f.get('name')} | 索引: {f.get('index')}"
            )
            print(
                f"  字符数: {f.get('word_count')} | 句子数: {f.get('sentence_count')} | 语言: {f.get('language')}"
            )
        print("-" * 60)

    # 2）打印主题信息
    themes = d.get("themes", [])
    if themes:
        print("【主题信息】")
        for i, t in enumerate(themes, 1):
            theme = t.get("theme")
            keywords = t.get("keywords") or []
            file_index = t.get("file_index")
            summary = t.get("summary", "")
            confidence = t.get("confidence", None)
            topic_index = t.get("topic_index", None)
            file_id = t.get("file_id", None)

            print(f"主题{i}: {theme}")
            print(f"  关键词: {', '.join(keywords)}")
            print(f"  所属文件索引: {file_index} | 文件ID: {file_id}")
            print(f"  主题序号(topic_index): {topic_index} | 置信度: {confidence}")
            if summary:
                print(f"  摘要: {summary}")
            # 如果你想看 keyword_details，可以打开注释：
            # for kd in t.get("keyword_details", []):
            #     print(f"    关键词详情: {kd}")
            print("-" * 40)

    # 3）打印统计信息
    stats = d.get("statistics") or {}
    if stats:
        print("【统计信息】")
        print(f"- 文件总数: {stats.get('file_count')}")
        print(f"- 主题总数: {stats.get('theme_count')}")
        print(f"- 处理耗时(ms): {stats.get('processing_time_ms')}")
        print(f"- 算法版本: {stats.get('algorithm_version')}")
        print("=" * 60)


def test_single_text():
    """测试单个文本（使用 'text' 字段）"""
    url = "http://127.0.0.1:5000/api/extract-interests"

    text = (
        "自2014年起参与“一带一路”倡议实施以来，"
        "李文浩先后在哈萨克斯坦、吉尔吉斯斯坦、乌兹别克斯坦等国承担多个重点项目的"
        "前期调研、商务谈判与执行管理任务。"
    )

    payload = {"text": text}
    resp = requests.post(url, json=payload, timeout=10)
    resp.raise_for_status()

    data = resp.json()
    print("====== 单文本测试结果 ======")
    pretty_print_single_response(data)


def test_multi_texts():
    """测试多文本（使用 'texts' 数组），方便你模拟多文档场景。"""
    url = "http://127.0.0.1:5000/api/extract-interests"

    texts = [
        "自2014年起参与“一带一路”倡议实施以来，李文浩先后在哈萨克斯坦等国承担多个重点项目的前期调研任务。",
        "在乌兹别克斯坦项目中，他主要负责商务谈判与执行管理，推动基础设施建设合作落地。",
    ]

    payload = {"texts": texts}
    resp = requests.post(url, json=payload, timeout=10)
    resp.raise_for_status()

    data = resp.json()
    print("====== 多文本测试结果 ======")
    pretty_print_single_response(data)


def main():
    # 按需注释/打开
    test_single_text()
    print("\n\n")
    test_multi_texts()


if __name__ == "__main__":
    main()
