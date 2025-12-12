from flask import Flask, request, jsonify
from flask_cors import CORS
import grpc
from interest.v1 import user_pb2 as pb2
from interest.v1 import user_pb2_grpc as pb2_grpc
import logging
import time

app = Flask(__name__)
CORS(app)

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def get_grpc_stub():
    channel = grpc.insecure_channel("127.0.0.1:50051")
    stub = pb2_grpc.InterestExtractorStub(channel)
    return stub

@app.route("/api/extract-interests", methods=["POST", "OPTIONS"])
def extract_interests():
    if request.method == "OPTIONS":
        return jsonify({"status": "ok"}), 200
        
    try:
        request_data = request.get_json()
        logger.info(f"收到请求，数据keys: {list(request_data.keys()) if request_data else '无数据'}")
        
        # 支持单文件和多文件两种格式
        if "text" in request_data:
            # 单文件格式（向后兼容）
            texts = [request_data["text"]]
        elif "texts" in request_data and isinstance(request_data["texts"], list):
            # 多文件格式
            texts = request_data["texts"]
        else:
            return jsonify({
                "code": 400, 
                "msg": "参数错误：请提供 'text' 字段或 'texts' 数组"
            }), 400
        
        if not texts:
            return jsonify({"code": 400, "msg": "参数错误：文本内容不能为空"}), 400
        
        # 处理每个文件的文本内容
        all_themes = []   # 汇总后的所有主题（兼容老结构 + 新字段）
        all_files = []    # 所有文件的信息列表
        total_theme_count = 0

        stub = get_grpc_stub()
        http_start_ts = time.time()
        
        for i, text in enumerate(texts):
            if not text or not text.strip():
                logger.warning(f"第 {i+1} 个文件内容为空，跳过处理")
                continue
                
            logger.info(f"处理第 {i+1} 个文件，内容长度: {len(text)}")
            
            try:
                # 调用 gRPC 服务处理单个文件
                grpc_request = pb2.ExtractRequest(text=text.strip())
                grpc_response = stub.ExtractInterests(grpc_request)

                # ========= 1) 收集单个文件信息（如果 gRPC 有返回 file 字段） =========
                if grpc_response.file.id:
                    f = grpc_response.file
                    all_files.append({
                        "id": f.id,
                        "name": f.name,
                        "index": f.index,
                        "word_count": f.word_count,
                        "sentence_count": f.sentence_count,
                        "language": f.language
                    })
                else:
                    # 兜底：如果服务端还没实现 FileInfo，也可以自己简单补一个
                    all_files.append({
                        "id": f"doc{i + 1}",
                        "name": f"文档{i + 1}",
                        "index": i + 1,
                        "word_count": 0,
                        "sentence_count": 0,
                        "language": "unknown"
                    })
                
                # ========= 2) 收集该文件的主题列表 =========
                file_themes = []
                for theme in grpc_response.themes:
                    # 结构化关键词（KeywordInfo）
                    keyword_details = []
                    # 如果服务端还没实现 keyword_infos，这里不会报错，只是空
                    for kw in theme.keyword_infos:
                        keyword_details.append({
                            "text": kw.text,
                            "weight": kw.weight,
                            "count": kw.count
                        })

                    file_index = theme.file_index if theme.file_index > 0 else (i + 1)
                    file_id = f"doc{file_index}"

                    file_themes.append({
                        # ====== 兼容旧前端的字段 ======
                        "theme": theme.theme,
                        "keywords": list(theme.keywords),
                        "file_index": file_index,

                        # ====== 新增字段，供新前端使用 ======
                        "id": theme.id or f"{file_id}-t{len(file_themes) + 1}",
                        "summary": theme.summary,
                        "keyword_details": keyword_details,
                        "confidence": theme.confidence,
                        "topic_index": theme.topic_index,
                        "file_id": file_id
                    })
                
                all_themes.extend(file_themes)
                total_theme_count += len(file_themes)
                logger.info(f"第 {i+1} 个文件提取到 {len(file_themes)} 个主题")
                
            except grpc.RpcError as e:
                logger.error(f"处理第 {i+1} 个文件时 gRPC 错误: {e.details()}")
                # 单个文件失败不影响其他文件
                continue
            except Exception as e:
                logger.error(f"处理第 {i+1} 个文件时未知错误: {str(e)}")
                continue
        
        http_end_ts = time.time()
        http_cost_ms = (http_end_ts - http_start_ts) * 1000.0

        # ========= 3) 构造统计信息 =========
        statistics = {
            "file_count": len(texts),
            "theme_count": total_theme_count,
            "processing_time_ms": http_cost_ms,
            # 如果 gRPC 那边 stats.algorithm_version 已实现，也可以从第一个响应里取
            "algorithm_version": "v1.1.0"
        }
        
        # 返回合并后的结果（兼容 + 扩展）
        response_data = {
            "code": 200,
            "msg": f"成功处理 {len(texts)} 个文件，提取到 {total_theme_count} 个主题",
            "data": {
                # ====== 旧字段（保持不变） ======
                "themes": all_themes,
                "file_count": len(texts),
                "theme_count": total_theme_count,

                # ====== 新增字段 ======
                "files": all_files,
                "statistics": statistics
            }
        }
        
        logger.info(f"请求处理完成，总共提取到 {total_theme_count} 个主题")
        return jsonify(response_data), 200
    
    except grpc.RpcError as e:
        logger.error(f"gRPC服务错误: {e.details()}")
        return jsonify({"code": 500, "msg": f"gRPC服务错误：{e.details()}"}), 500
    except Exception as e:
        logger.error(f"服务器错误: {str(e)}")
        return jsonify({"code": 500, "msg": f"服务器错误：{str(e)}"}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
