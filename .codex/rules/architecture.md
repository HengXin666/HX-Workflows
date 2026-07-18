# Project architecture

- Keep the three product boundaries independent: `bilibili-api`, `emall`, and `tg`.
- Put Telegram scheduling and dependency ordering in `tg/runner.py`; keep provider actions in `tg/scripts/`.
- Keep `tg/web` local-only. Browser code must call the local adapter and must never contain Telegram or SMTP credentials.
- External Bilibili, Telegram, SMTP, and GitHub calls need adapters that deterministic tests can replace.
- Existing large files are an advisory baseline. Split a file when modifying it would make the business boundary clearer; do not refactor unrelated history.
- New project behavior must add assertions for its success, rejection, and recoverable failure paths.
