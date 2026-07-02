#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WEB_DIR="$ROOT/web"
LOG_DIR="${TG_LOG_DIR:-$ROOT/logs}"
API_HOST="${TG_WEB_API_HOST:-127.0.0.1}"
API_PORT="${TG_WEB_API_PORT:-8765}"
WEB_HOST="${TG_WEB_HOST:-127.0.0.1}"
WEB_PORT="${TG_WEB_PORT:-5173}"
TG_PROXY="${TG_PROXY:-http://127.0.0.1:2334}"
PID_FILE="${TG_WEB_PID_FILE:-$LOG_DIR/web.pid}"
API_PID=""
WEB_PID=""

cleanup() {
  if [[ -n "$WEB_PID" ]] && kill -0 "$WEB_PID" 2>/dev/null; then
    kill "$WEB_PID" 2>/dev/null || true
  fi
  if [[ -n "$API_PID" ]] && kill -0 "$API_PID" 2>/dev/null; then
    kill "$API_PID" 2>/dev/null || true
  fi
  rm -f "$PID_FILE"
}

trap cleanup EXIT INT TERM

stop_pid() {
  local pid="$1"
  if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
    echo "Stopping previous TG web process: $pid"
    kill "$pid" 2>/dev/null || true
    for _ in {1..20}; do
      if ! kill -0 "$pid" 2>/dev/null; then
        return
      fi
      sleep 0.1
    done
    kill -9 "$pid" 2>/dev/null || true
  fi
}

stop_port_processes() {
  local port="$1"
  local pids=""
  if command -v lsof >/dev/null 2>&1; then
    pids="$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)"
  elif command -v fuser >/dev/null 2>&1; then
    pids="$(fuser "$port"/tcp 2>/dev/null || true)"
  fi
  for pid in $pids; do
    stop_pid "$pid"
  done
}

stop_previous() {
  if [[ -f "$PID_FILE" ]]; then
    while read -r pid; do
      stop_pid "$pid"
    done < "$PID_FILE"
    rm -f "$PID_FILE"
  fi
  stop_port_processes "$API_PORT"
  stop_port_processes "$WEB_PORT"
}

need_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing command: $1" >&2
    exit 1
  fi
}

need_cmd uv
need_cmd npm

mkdir -p "$LOG_DIR"
stop_previous

cd "$ROOT"
uv sync

cd "$WEB_DIR"
if [[ ! -d node_modules ]]; then
  npm install
fi

cd "$ROOT"
uv run python scripts/local_web_api.py \
  --host "$API_HOST" \
  --port "$API_PORT" \
  --out "$ROOT/sessions/tg_session_strings.txt" \
  --main-out "$ROOT/sessions/tg_main_session_string.txt" \
  --workflows "$ROOT/sessions/web_workflows.json" &
API_PID="$!"

cd "$WEB_DIR"
VITE_TG_WEB_API="http://$API_HOST:$API_PORT" npm run dev -- --host "$WEB_HOST" --port "$WEB_PORT" &
WEB_PID="$!"

printf "%s\n%s\n" "$API_PID" "$WEB_PID" > "$PID_FILE"

echo
echo "TG local web is running:"
echo "  Web: http://$WEB_HOST:$WEB_PORT"
echo "  API: http://$API_HOST:$API_PORT"
echo "  Proxy: $TG_PROXY"
echo
echo "Press Ctrl+C to stop both services."

wait "$API_PID" "$WEB_PID"
