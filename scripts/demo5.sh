#!/usr/bin/env bash
# ch05 验收。前置：MySQL、Milvus 已启动，已 build_kb，服务已启动且日志写到文件。
# 用法：bash scripts/demo5.sh <服务日志路径>
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
BASE="${BASE_URL:-http://127.0.0.1:8000}"
LOG="${1:?用法：bash scripts/demo5.sh <服务日志路径>}"
USER_ID="demo5-$(date +%s)-$RANDOM"

if ! curl --fail --silent --connect-timeout 5 --max-time 10 "$BASE/health" >/dev/null 2>&1; then
    echo '请先启动服务：uv run uvicorn app.main:app --port 8000 > <日志> 2>&1' >&2
    exit 1
fi

mysql_q() {
    docker exec aftersales-mysql mysql -N --default-character-set=utf8mb4 -uaftersales -paftersales aftersales -e "$1"
}

# ask <消息> [会话ID]：打印 SSE 原文
ask() {
    local body
    body=$(uv run python -c 'import json,sys; d={"user_id":sys.argv[1],"message":sys.argv[2]}; sys.argv[3:] and d.update(session_id=sys.argv[3]); print(json.dumps(d, ensure_ascii=False))' "$USER_ID" "$@")
    curl --fail --silent --show-error -N -X POST "$BASE/chat/stream" -H 'Content-Type: application/json' -d "$body"
}

# sse_get <SSE 原文> <python 表达式>：events 为 [(name, data)]
sse_get() {
    printf '%s\n' "$1" | uv run python -c '
import json, sys
events, name = [], None
for line in sys.stdin.read().splitlines():
    if line.startswith("event: "): name = line[7:]
    elif line.startswith("data: "): events.append((name, json.loads(line[6:])))
print(eval(sys.argv[1]))' "$2"
}

fail() { echo "❌ $1"; exit 1; }

echo '=== 验收 1：政策问题走强制检索 ==='
sse=$(ask '退货运费谁出？')
cid=$(sse_get "$sse" 'events[0][1]["session_id"]')
grep -q "node=retrieve conversation=$cid" "$LOG" || fail "日志中没有 node=retrieve conversation=$cid"
echo "日志：$(grep "node=retrieve conversation=$cid" "$LOG" | tail -1)"
echo "回复：$(sse_get "$sse" '"".join(d["text"] for n, d in events if n == "token")')"
echo '✅ 验收 1 通过'

echo '=== 验收 2：Agent 自己调工具查物流 ==='
sse=$(ask '订单 1001 的物流到哪了')
tools=$(sse_get "$sse" '[t["name"] for n, d in events if n == "tool_start" for t in d["tools"]]')
echo "调用的工具：$tools"
[[ "$tools" == *query_logistics* ]] || fail '没有调用 query_logistics'
echo "回复：$(sse_get "$sse" '"".join(d["text"] for n, d in events if n == "token")')"
echo '✅ 验收 2 通过'

echo '=== 验收 3：投诉给出两个独立选项，点了才建单 ==='
sse=$(ask '我要投诉，你们快递员态度太差了')
cid=$(sse_get "$sse" 'events[0][1]["session_id"]')
options=$(sse_get "$sse" '[o["type"] for n, d in events if n == "actions" for o in d["options"]]')
echo "选项：$options"
[[ "$options" == "['handoff', 'ticket']" ]] || fail '投诉没有给出 handoff 和 ticket 两个选项'
before=$(mysql_q "SELECT COUNT(*) FROM tickets WHERE conversation_id = $cid")
ask '那算了，先帮我查下订单 1001' "$cid" >/dev/null
after_chat=$(mysql_q "SELECT COUNT(*) FROM tickets WHERE conversation_id = $cid")
[[ "$before" == "$after_chat" ]] || fail '用户没点按钮，tickets 表却有新增'
echo "不点按钮继续对话：tickets 行数 $before → $after_chat"
resp=$(curl --fail --silent --show-error -X POST "$BASE/tickets" -H 'Content-Type: application/json' \
    -d "{\"session_id\":\"$cid\",\"user_id\":\"$USER_ID\",\"description\":\"快递员态度太差\",\"ticket_type\":\"投诉\"}")
echo "点「建工单」：$resp"
after_click=$(mysql_q "SELECT COUNT(*) FROM tickets WHERE conversation_id = $cid")
[[ "$after_click" == "$((before + 1))" ]] || fail '点击建工单后 tickets 表没有新增 1 行'
echo '「转人工」只在前端模拟，请在浏览器中确认。'
echo '✅ 验收 3 通过（后端部分）'

echo '=== 验收 4：闲聊回固定话术 ==='
sse=$(ask '你好呀')
reply=$(sse_get "$sse" '"".join(d["text"] for n, d in events if n == "token")')
expected=$(uv run python -c 'from app.prompts import CHITCHAT_REPLY; print(CHITCHAT_REPLY)')
echo "回复：$reply"
[[ "$reply" == "$expected" ]] || fail '闲聊回复不是固定话术'
echo '✅ 验收 4 通过'

echo '=== 验收 5：复杂问题 ReAct 走多步 ==='
sse=$(ask '帮我查下订单 1001 买的是什么，如果已经发货了，再看看物流到哪了')
cid=$(sse_get "$sse" 'events[0][1]["session_id"]')
line=$(grep "turn conversation=$cid " "$LOG" | tail -1)
echo "日志：$line"
steps=$(printf '%s' "$line" | sed -E 's/.* steps=([0-9]+).*/\1/')
[[ "$steps" -ge 2 ]] || fail "ReAct 只走了 $steps 步"
echo "回复：$(sse_get "$sse" '"".join(d["text"] for n, d in events if n == "token")')"
echo '✅ 验收 5 通过'

echo '全部验收通过。'
