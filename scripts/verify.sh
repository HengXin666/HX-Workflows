#!/usr/bin/env bash
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"

echo "==> static: Python syntax"
python3 -m compileall -q bilibili-api/bilibili_api emall tg/runner.py tg/scripts

echo "==> static: repository architecture (advisory baseline)"
python3 scripts/check_arch.py --max-output 20 bilibili-api/bilibili_api emall tg

echo "==> static: changed TG Python lint"
uv run --project tg ruff check tg/runner.py tg/scripts tg/tests

echo "==> S1/S2/S3: TG rules, adapters, and local HTTP application"
uv run --project tg python -m unittest discover -s tg/tests -v

echo "==> S1/S2: email configuration, escaping, and SMTP adapter"
uv run --project emall python -m unittest discover -s emall/tests -v

echo "==> S1: Bilibili signing and filename safety"
uv run --project bilibili-api python -m unittest discover -s bilibili-api/tests -v

echo "==> static/S3: React TypeScript production build"
npm --prefix tg/web run build

echo "PASS repository verification"
