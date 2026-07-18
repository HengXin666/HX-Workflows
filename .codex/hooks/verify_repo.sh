#!/usr/bin/env bash
set -uo pipefail

INPUT="$(cat || true)"
grep -qE '"stop_hook_active"[[:space:]]*:[[:space:]]*true' <<<"$INPUT" && exit 0
ROOT="$(git rev-parse --show-toplevel 2>/dev/null || echo "$PWD")"
GIT_LOG_DIR="$(git -C "$ROOT" rev-parse --git-path hx-init/logs 2>/dev/null || true)"
[[ "$GIT_LOG_DIR" = /* ]] || GIT_LOG_DIR="$ROOT/$GIT_LOG_DIR"
mkdir -p "$GIT_LOG_DIR"
LOG="$GIT_LOG_DIR/repository-verify-latest.log"

if bash "$ROOT/scripts/verify.sh" >"$LOG" 2>&1; then
  exit 0
fi
SAMPLES="$(grep -Ei 'error|fail|traceback|warning' "$LOG" | head -5 | tr '\n' ' ' || true)"
printf '{"decision":"block","reason":"Repository verification failed. log=%s; samples=%s"}\n' "$LOG" "${SAMPLES//\"/\\\"}"
exit 0
