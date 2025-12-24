# Repository Guidelines

## Project Structure & Module Organization
- `api_adapter.py`: Flask entrypoint for `/api/extract-interests` and `/api/recent-docs`; validates payloads, guesses language, and tracks in-memory `recent_files`.
- `app_logic.py`: NLP core (sentence splitting, jieba/regex tokenization, TF-IDF + KMeans themes, auto top-k sizing).
- `http_client.py`: Smoke-test client that posts sample texts and pretty-prints responses.
- `requirements.txt`: Runtime deps; `readme.txt`: Chinese quickstart. Ignore workspace artifacts like `.venv` and `__pycache__/`.

## Build, Test, and Development Commands
- `python -m venv .venv && .venv\Scripts\activate` (Windows) or `source .venv/bin/activate` (Linux/macOS).
- `pip install -r requirements.txt` to pull Flask, CORS, jieba, and scikit-learn.
- `python api_adapter.py` to run the HTTP service on `0.0.0.0:5000` with CORS enabled.
- `python http_client.py` for quick regression checks; exercises single and multi-text payloads.
- Curl example: `curl -X POST http://127.0.0.1:5000/api/extract-interests -H "Content-Type: application/json" -d "{\"text\": \"示例文本\"}"`.

## Coding Style & Naming Conventions
- Python 3.x, 4-space indent, UTF-8. Use `snake_case` and keep type hints as in `app_logic.py`.
- Preserve response contracts (`files`, `themes`, `statistics`, `code/msg`); extend in a backward-compatible way.
- Prefer `logging` over prints; keep log lines short and non-sensitive. Maintain existing Chinese user-facing strings unless intentionally changed.

## Testing Guidelines
- No formal suite yet; rely on `python http_client.py` to verify `files`/`themes`/`statistics` fields after changes.
- If adding automated tests, place them under `tests/` and prefer `pytest` with deterministic sample texts.
- Manually cover edge cases: empty payloads, mixed-language input, multi-document submissions with blanks.

## Commit & Pull Request Guidelines
- Commit messages: imperative, one concern, e.g., `Refine theme tokenization`.
- PRs should state scope, before/after behavior (response snippets), and reproduction steps (`python api_adapter.py` + `python http_client.py` or curl).
- Link issues when available; call out API contract changes and expected client impact. JSON snippets beat screenshots for API evidence.

## Security & Configuration Tips
- Service binds to all interfaces with open CORS; avoid exposing it publicly without a reverse proxy or auth.
- Dependencies are pinned; check scikit-learn/jieba compatibility before upgrading.
- Do not log full request bodies or long texts to reduce data leakage risk.

## 注释与回复

- 统一使用中文回复.
- 代码添加中文注释.