#!/usr/bin/env bash
set -euo pipefail

BASE="${BASE_URL:-http://127.0.0.1:8000}"

if ! curl --fail --silent --show-error --connect-timeout 5 --max-time 10 \
    "$BASE/health" >/dev/null 2>&1; then
    printf '%s\n' '请先启动服务：uv run uvicorn app.main:app --port 8000' >&2
    exit 1
fi

USER_ID="demo-$(date +%s)"
SSE_FILE="$(mktemp)"
trap 'rm -f "$SSE_FILE"' EXIT

printf '%s\n' '=== 验收 1：流式回复 ==='
curl --fail --silent --show-error -N -X POST "$BASE/chat/stream" \
    -H 'Content-Type: application/json' \
    -d "{\"user_id\":\"$USER_ID\",\"message\":\"你好，我上周买的耳机左耳没声音了\"}" \
    | tee "$SSE_FILE"
printf '\n\n'

SESSION_ID="$(python3 -c '
import json
import sys

lines = sys.stdin.read().splitlines()
for index, line in enumerate(lines[:-1]):
    if line.strip() == "event: session":
        data = lines[index + 1]
        if data.startswith("data:"):
            session_id = json.loads(data[5:].strip()).get("session_id")
            if isinstance(session_id, str) and session_id.isascii() and session_id.isdigit():
                print(session_id)
                sys.exit(0)
        break
sys.exit("未能从 session 事件读取有效的 session_id")
' < "$SSE_FILE")"

printf '%s\n' '=== 验收 2：会话记忆 ==='
curl --fail --silent --show-error -N -X POST "$BASE/chat/stream" \
    -H 'Content-Type: application/json' \
    -d "{\"user_id\":\"$USER_ID\",\"session_id\":\"$SESSION_ID\",\"message\":\"我刚才说的是哪个商品？\"}"
printf '\n\n'

printf '%s\n' '=== 验收 3：售后信息提取 ==='
curl --fail --silent --show-error -X POST "$BASE/extract" \
    -H 'Content-Type: application/json' \
    -d '{"text":"订单号 A12345 的耳机左耳没声音，我想换个新的。"}'
printf '\n\n'
