#!/usr/bin/env bash
set -uo pipefail

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || echo "$PWD")"
INPUT="$(cat || true)"
PYTHON="$(command -v python3 || command -v python || true)"
[[ -n "$PYTHON" ]] || exit 0

while IFS= read -r path; do
  [[ -n "$path" ]] || continue
  [[ "$path" = "$ROOT"/* ]] && path="${path#"$ROOT"/}"
  [[ "$path" = /* || "$path" = ..* || "$path" = *"/../"* ]] && continue
  [[ -f "$ROOT/$path" ]] || continue
  "$PYTHON" - "$ROOT/$path" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
try:
    text = path.read_text(encoding="utf-8")
except (UnicodeDecodeError, OSError):
    raise SystemExit(0)
clean = "\n".join(line.rstrip() for line in text.replace("\ufeff", "").replace("\u200b", "").splitlines())
if text.endswith("\n"):
    clean += "\n"
if clean != text:
    path.write_text(clean, encoding="utf-8")
PY
done < <(HOOK_INPUT="$INPUT" "$PYTHON" - <<'PY'
import json
import os
import re

try:
    value = json.loads(os.environ.get("HOOK_INPUT", "") or "{}")
except json.JSONDecodeError:
    value = {}
paths = []
def walk(item):
    if isinstance(item, dict):
        for key, child in item.items():
            if key.lower() in {"file_path", "filepath", "path", "filename"} and isinstance(child, str):
                paths.append(child)
            else:
                walk(child)
    elif isinstance(item, list):
        for child in item:
            walk(child)
    elif isinstance(item, str):
        paths.extend(re.findall(r"^\*\*\* (?:Add|Update) File: (.+)$", item, re.M))
walk(value)
print("\n".join(dict.fromkeys(paths)))
PY
)
