#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

BASE="${BASE_URL:-http://127.0.0.1:8000}"

if ! curl --fail --silent --show-error --connect-timeout 5 --max-time 10 \
    "$BASE/health" >/dev/null 2>&1; then
    printf '%s\n' '请先启动服务：uv run uvicorn app.main:app --port 8000' >&2
    exit 1
fi

printf '%s\n' '=== 验收 2：建库中断与重跑 ==='
if uv run python scripts/build_kb.py --rebuild --crash-after-batches 2; then
    crash_exit=0
else
    crash_exit=$?
fi
if [[ "$crash_exit" -ne 1 ]]; then
    printf '%s\n' '验收失败：建库中断的退出码必须为 1' >&2
    exit 1
fi

uv run python scripts/build_kb.py
if uv run python scripts/build_kb.py --check; then
    check_exit=0
else
    check_exit=$?
fi
if [[ "$check_exit" -ne 0 ]]; then
    printf '%s\n' '验收失败：建库检查的退出码必须为 0' >&2
    exit 1
fi

printf '%s\n' '=== 对话挖掘 ==='
DAY=$(date -v-1d +%F)
uv run python scripts/seed_history.py --date "$DAY"
uv run python scripts/mine_qa.py --date "$DAY"
docker exec aftersales-mysql mysql --default-character-set=utf8mb4 \
    -uaftersales -paftersales aftersales \
    -e "SELECT questions, category, vectorize_status FROM knowledge_chunks WHERE content_type='mined' ORDER BY id"

printf '%s\n' '=== 验收 1：邮费是多少 ==='
USER_ID="demo3-$(date +%s)"
sse_output="$(curl --fail --silent --show-error -N -X POST "$BASE/chat/stream" \
    -H 'Content-Type: application/json' \
    -d "{\"user_id\":\"$USER_ID\",\"message\":\"邮费是多少\"}")"

parsed_output="$(printf '%s\n' "$sse_output" | python3 -c '
import json
import sys

session_id = None
tokens = []
lines = sys.stdin.read().splitlines()
for index, line in enumerate(lines[:-1]):
    if not line.startswith("event:"):
        continue
    event = line[6:].strip()
    data_line = lines[index + 1]
    if not data_line.startswith("data:"):
        sys.exit("验收失败：SSE 事件缺少数据")
    try:
        data = json.loads(data_line[5:].strip())
    except json.JSONDecodeError:
        sys.exit("验收失败：SSE 事件数据格式错误")
    if not isinstance(data, dict):
        sys.exit("验收失败：SSE 事件数据格式错误")
    if event == "error":
        sys.exit("验收失败：对话服务返回错误事件")
    if event == "session":
        session_id = data.get("session_id")
    elif event == "token":
        token = data.get("text")
        if not isinstance(token, str):
            sys.exit("验收失败：token 事件缺少文本")
        tokens.append(token)

if not (isinstance(session_id, str) and session_id.isascii() and session_id.isdigit()):
    sys.exit("验收失败：未能读取有效的 session_id")
reply = "".join(tokens)
if not reply:
    sys.exit("验收失败：回复为空")
print(session_id)
sys.stdout.write(reply)
')"
session_id="${parsed_output%%$'\n'*}"
reply="${parsed_output#*$'\n'}"
printf '%s\n' "$reply"

printf '%s\n' '本会话的工具检索结果：'
docker exec aftersales-mysql mysql --default-character-set=utf8mb4 \
    -uaftersales -paftersales aftersales \
    -e "SELECT content FROM messages WHERE conversation_id=$session_id AND role='tool' ORDER BY id"

printf '%s\n' "$reply" | python3 -c '
import re
import sys

reply = sys.stdin.read()
if "99" not in reply or re.search(r"8\s*元", reply) is None:
    sys.exit("验收失败：回复必须包含 99 和 8 元")
'

printf '%s\n' 'ch03 验收全部通过'
