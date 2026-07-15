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

## 会话与数据隔离

- 登录成功后由 Flask 签名 Cookie 保存会话，前端请求必须携带 `credentials: include`。
- `GET /api/auth/me` 用于恢复当前登录用户。
- `POST /api/auth/logout` 用于清除会话。
- `/extract` 和 `/task` 相关接口均只允许访问当前登录用户的数据。
- 生产环境必须固定设置 `APP_SECRET_KEY`，并在 HTTPS 下设置 `SESSION_COOKIE_SECURE=true`。
- `CORS_ORIGINS` 应只填写实际部署的前端地址，多个地址使用英文逗号分隔。

## 回归测试

```powershell
python -m unittest tests.test_auth_isolation -v
```
