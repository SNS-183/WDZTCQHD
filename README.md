# WDZTCQHD 文档主题抽取后端

## 本地启动

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

$env:DB_HOST = '127.0.0.1'
$env:DB_PORT = '3306'
$env:DB_USER = 'root'
$env:DB_PASSWORD = '请填写数据库密码'
$env:DB_NAME = 'wdztcqhdfw'
$env:APP_SECRET_KEY = '请设置足够长的随机密钥'
$env:CORS_ORIGINS = 'http://127.0.0.1:5173,http://localhost:5173'
$env:ADMIN_USERNAMES = 'admin'
# 可选：扫描 PDF 无文本层时调用的 OCR 服务，需返回 {"text": "识别正文"}
$env:OCR_SERVICE_URL = 'http://127.0.0.1:8000/ocr'

python api_adapter.py
```

生产环境请使用 Waitress 启动，Flask Debug 默认关闭：

```powershell
$env:APP_SECRET_KEY = '请设置足够长的随机密钥'
waitress-serve --host=127.0.0.1 --port=5000 api_adapter:app
```

可通过 `GET /api/health` 执行存活探测。上传边界可用
`MAX_REQUEST_BYTES`、`MAX_EXTRACT_FILE_COUNT`、`MAX_SINGLE_FILE_BYTES`、
`MAX_TOTAL_TEXT_CHARS`、`MAX_PDF_PAGE_COUNT` 和 `MAX_DOCX_UNCOMPRESSED_BYTES` 调整。

## 会话与数据隔离

- 登录成功后由 Flask 签名 Cookie 保存会话，前端请求必须携带 `credentials: include`。
- `GET /api/auth/me` 用于恢复当前登录用户。
- `POST /api/auth/logout` 用于清除会话。
- `/extract` 和 `/task` 相关接口均只允许访问当前登录用户的数据。
- 生产环境必须固定设置 `APP_SECRET_KEY`，并在 HTTPS 下设置 `SESSION_COOKIE_SECURE=true`。
- `CORS_ORIGINS` 应只填写实际部署的前端地址，多个地址使用英文逗号分隔。

## 任务管理、抽取质量与分享

- `GET /task` 支持 `page`、`page_size`、`keyword`、`status`、`days`、`sort` 和
  `focus_task_id`、`archived`；其中 `archived` 支持 `active/archived/all`。
- `PATCH /task/<task_id>` 更新任务名称、标签和归档状态。
- `POST /task/batch` 执行批量归档、恢复、打标签或删除。
- `POST /task/<task_id>/copy` 复制完整分析批次；`POST /task/<task_id>/retry`
  返回失败任务的安全重跑请求。
- `GET /task/<task_id>/audit` 返回当前用户任务审计记录；
  `GET /api/admin/statistics` 仅向管理员返回只读聚合统计。
- `PATCH /task/<task_id>/topics/<topic_id>` 重命名当前用户任务内的主题。
- `PATCH /task/<task_id>/topics/<topic_id>/confirmation` 持久化人工确认状态。
- `DELETE /task/<task_id>/topics/<topic_id>` 删除主题并刷新批次统计。
- `POST /task/<task_id>/topics/merge` 合并同一文档内的多个主题。
- `/extract` 支持 `topic_k`、`topn_keywords`、`granularity`、
  `custom_stopwords` 和 `domain_terms`，结果返回算法版本和质量指标。
- `POST /task/compare` 对比 2~5 个任务；`GET/POST /task/filters` 管理保存筛选。
- `POST /task/<task_id>/share` 创建 1~30 天只读分享；`GET /share/<token>`
  无需登录读取已脱敏的分享结果。

扫描 PDF 优先读取文本层；无文本层且配置 `OCR_SERVICE_URL` 时自动调用 OCR。
OCR 请求体为 `{"content_base64": "..."}`，响应体需包含 `text` 字段。生产环境应只配置
受信任的内网 OCR 服务，并通过 `OCR_TIMEOUT_SECONDS` 设置超时。

## 回归测试

```powershell
python -m unittest discover -s tests -v
```
