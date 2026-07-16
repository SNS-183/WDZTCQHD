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

## 任务检索与主题编辑

- `GET /task` 支持 `page`、`page_size`、`keyword`、`status`、`days`、`sort` 和
  `focus_task_id`；其中 `days` 支持 `0/7/30/90`，`sort` 支持 `newest/oldest`。
- `PATCH /task/<task_id>/topics/<topic_id>` 重命名当前用户任务内的主题。
- `DELETE /task/<task_id>/topics/<topic_id>` 删除主题并刷新批次统计。
- `POST /task/<task_id>/topics/merge` 合并同一文档内的多个主题。

## 回归测试

```powershell
python -m unittest tests.test_auth_isolation -v
```
