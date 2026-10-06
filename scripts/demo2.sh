#!/usr/bin/env bash
set -euo pipefail

BASE="${BASE_URL:-http://127.0.0.1:8000}"

if ! curl --fail --silent --show-error --connect-timeout 5 --max-time 10 \
    "$BASE/health" >/dev/null 2>&1; then
    printf '%s\n' '请先启动服务：uv run uvicorn app.main:app --port 8000' >&2
    exit 1
fi

USER_ID="demo2-$(date +%s)"

run_case() {
    local number="$1"
    local message="$2"
    local sse_output session_id

    printf '=== 验收 %s：%s ===\n' "$number" "$message"
    sse_output="$(curl --fail --silent --show-error -N -X POST "$BASE/chat/stream" \
        -H 'Content-Type: application/json' \
        -d "{\"user_id\":\"$USER_ID\",\"message\":\"$message\"}")"
    printf '%s\n\n' "$sse_output"

    session_id="$(printf '%s\n' "$sse_output" | python3 -c '
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
')"

    printf '%s\n' '本会话写入的消息：'
    docker exec aftersales-mysql mysql --default-character-set=utf8mb4 \
        -uaftersales -paftersales aftersales \
        -e "SELECT role, LEFT(REPLACE(content, '
', ' '), 60) AS content, JSON_UNQUOTE(JSON_EXTRACT(tool_calls, '\$[*].name')) AS tool_calls, tool_call_id FROM messages WHERE conversation_id=$session_id ORDER BY id"
    printf '\n'
}

run_case 1 '订单 1001 的物流到哪了'
run_case 2 '退货政策是什么'
run_case 3 '邮费是多少'
