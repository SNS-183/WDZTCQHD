from flask import Flask, request, jsonify
from flask_cors import CORS
import logging
import time

from app_logic import extract_themes_from_text, split_sentences  # 注意：split_sentences 也从 app_logic 引入

app = Flask(__name__)
CORS(app)

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ================== 全局“最近文件列表” ==================
# 仅在服务运行期间有效，用于首页“最近上传文档”展示
recent_files = []     # 每个元素结构类似：
# {
#     "id": "doc1",
#     "name": "文档1",
#     "index": 1,
#     "word_count": 120,
#     "sentence_count": 5,
#     "language": "zh",
#     "upload_time": "2025-11-17 10:32:45"
# }
MAX_RECENT = 20       # 最多保留最近 20 条


def guess_language(text: str) -> str:
    """非常粗糙的语言检测：中文字符比例 > 0.3 就当中文."""
    if not text:
        return "unknown"
    total = len(text)
    cn = sum("\u4e00" <= ch <= "\u9fff" for ch in text)
    ratio = cn / max(total, 1)
    return "zh" if ratio > 0.3 else "en"


@app.route("/api/extract-interests", methods=["POST", "OPTIONS"])
def extract_interests():
    # 处理预检请求（CORS 用）
    if request.method == "OPTIONS":
        return jsonify({"status": "ok"}), 200

    try:
        request_data = request.get_json(silent=True)
        logger.info(
            f"收到请求，数据keys: {list(request_data.keys()) if isinstance(request_data, dict) else '无/非JSON'}"
        )

        if not isinstance(request_data, dict):
            return jsonify({"code": 400, "msg": "请求体必须为 JSON 对象"}), 400

        # 支持单文件和多文件两种格式
        if "text" in request_data:
            texts = [request_data["text"]]
        elif "texts" in request_data and isinstance(request_data["texts"], list):
            texts = request_data["texts"]
        else:
            return jsonify({
                "code": 400,
                "msg": "参数错误：请提供 'text' 字段或 'texts' 数组"
            }), 400

        if not texts:
            return jsonify({"code": 400, "msg": "参数错误：文本内容不能为空"}), 400

        http_start_ts = time.time()

        # ====== 新增：文件列表 & 统计 ======
        all_files = []
        all_themes = []
        total_theme_count = 0

        for i, text in enumerate(texts):
            if text is None or not str(text).strip():
                logger.warning(f"第 {i + 1} 个文件内容为空，跳过处理")
                continue

            text = str(text)
            logger.info(f"处理第 {i + 1} 个文件，内容长度: {len(text)}")

            try:
                # ---- 1) 计算文件级信息 ----
                file_index = i + 1
                file_id = f"doc{file_index}"
                language = guess_language(text)
                sentences = split_sentences(text)
                word_count = len(text)
                sentence_count = len(sentences)

                upload_time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())

                file_info = {
                    "id": file_id,
                    "name": f"文档{file_index}",  # 如果前端有真实文件名，可以自己覆盖
                    "index": file_index,
                    "word_count": word_count,
                    "sentence_count": sentence_count,
                    "language": language,
                    # 新增字段：文件“上传/请求时间”
                    "upload_time": upload_time_str,
                }
                all_files.append(file_info)

                # ---- 1.5) 保存到全局 recent_files，供首页“最近上传文档”使用 ----
                recent_files.append(file_info)
                # 控制最大长度（只保留最近 MAX_RECENT 条）
                if len(recent_files) > MAX_RECENT:
                    recent_files.pop(0)  # 删除最旧的一条

                # ---- 2) 提取主题（沿用你原来的逻辑）----
                themes_raw = extract_themes_from_text(text, n_themes=3, topk=3)

                file_themes = []
                for t_idx, t in enumerate(themes_raw, start=1):
                    theme_word = t.get("theme", "")
                    keywords = list(t.get("keywords", []))

                    # 主题 id：doc1-t1 这种形式
                    theme_id = f"{file_id}-t{t_idx}"

                    # 简单给一个摘要：取文本前若干个字符
                    summary = text[:80] + ("..." if len(text) > 80 else "")

                    # 关键词详情：目前没有 TF-IDF 权重，这里简单给占位字段
                    keyword_details = []
                    for kw in keywords:
                        keyword_details.append({
                            "text": kw,
                            "weight": 1.0,  # 如果后面你在 app_logic 里能拿到 TF-IDF 权重，就改成真实值
                            "count": text.count(kw),
                        })

                    file_themes.append({
                        # 旧字段（兼容老前端）
                        "theme": theme_word,
                        "keywords": keywords,
                        "file_index": file_index,

                        # 新字段（对齐你 gRPC 版的结构）
                        "id": theme_id,
                        "summary": summary,
                        "keyword_details": keyword_details,
                        "confidence": 0.8,   # 现在没有真实置信度，先给一个固定值，占位
                        "topic_index": t_idx,
                        "file_id": file_id,
                    })

                all_themes.extend(file_themes)
                total_theme_count += len(file_themes)
                logger.info(f"第 {i + 1} 个文件提取到 {len(file_themes)} 个主题")

            except Exception as e:
                logger.error(f"处理第 {i + 1} 个文件时错误: {str(e)}")
                # 单个文件失败不影响其他文件
                continue

        http_end_ts = time.time()
        http_cost_ms = (http_end_ts - http_start_ts) * 1000.0

        statistics = {
            "file_count": len(texts),
            "theme_count": total_theme_count,
            "processing_time_ms": http_cost_ms,
            "algorithm_version": "http-v1.0.0",  # 你可以根据需要改成和模型/算法版本一致
        }

        response_data = {
            "code": 200,
            "msg": f"成功处理 {len(texts)} 个文件，提取到 {total_theme_count} 个主题",
            "data": {
                # 旧字段（保持不变，方便你现有前端使用）
                "themes": all_themes,
                "file_count": len(texts),
                "theme_count": total_theme_count,

                # 新增字段（对齐 gRPC 版）
                "files": all_files,
                "statistics": statistics,
            }
        }

        logger.info(f"请求处理完成，总共提取到 {total_theme_count} 个主题")
        return jsonify(response_data), 200

    except Exception as e:
        logger.error(f"服务器错误: {str(e)}")
        return jsonify({"code": 500, "msg": f"服务器错误：{str(e)}"}), 500


# ================== 新增：获取最近上传文档列表 ==================
@app.route("/api/recent-docs", methods=["GET"])
def get_recent_docs():
    """
    返回最近上传/处理的文档列表（供首页使用）
    """
    try:
        # recent_files 中越新的在越后面，这里反转一下，让最新的排在前面
        data = list(reversed(recent_files))

        return jsonify({
            "code": 200,
            "msg": "获取成功",
            "data": data
        }), 200

    except Exception as e:
        logger.error(f"获取 recent-docs 错误: {str(e)}")
        return jsonify({"code": 500, "msg": f"服务器错误：{str(e)}"}), 500


if __name__ == "__main__":
    # 直接把这个 Flask 服务当成主后端
    app.run(host="0.0.0.0", port=5000, debug=True)
