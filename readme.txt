

基于 HTTP 的用户兴趣主题抽取服务：输入用户文档字符串，输出 3 个主题词，每个主题词含 3 个关键词。

## 1. 快速开始（本地）

```bash
python -m venv .venv && .venv\Scripts\activate  # Windows
# 或 source .venv/bin/activate                  # Linux / macOS

pip install -r requirements.txt
set DB_HOST=127.0.0.1
set DB_PORT=3306
set DB_USER=root
set DB_PASSWORD=你的MySQL密码
set DB_NAME=wdztcqhdfw
python api_adapter.py
