#!/usr/bin/env bash
# ch06 验收。前置：MySQL、Milvus 已启动，已 build_kb，服务已启动且日志写到文件。
# 用法：bash scripts/demo6.sh <服务日志路径>
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

# 物流问题需要 logistics MCP Server。
if ! (exec 3<>/dev/tcp/127.0.0.1/8101) 2>/dev/null; then
    echo '请先启动物流服务：uv run python -m mcp_servers.logistics' >&2
    exit 1
fi
BASE="${BASE_URL:-http://127.0.0.1:8000}"
LOG="${1:?用法：bash scripts/demo6.sh <服务日志路径>}"
USER_ID="demo6-$(date +%s)-$RANDOM"

if ! curl --fail --silent --connect-timeout 5 --max-time 10 "$BASE/health" >/dev/null 2>&1; then
    echo '请先启动服务：uv run uvicorn app.main:app --port 8000 > <日志> 2>&1' >&2
    exit 1
fi

# ask <消息> [会话ID]：打印 SSE 原文；curl 同步读完后才开始下一轮。
ask() {
    local body
    body=$(python3 -c 'import json,sys; d={"user_id":sys.argv[1],"message":sys.argv[2]}; sys.argv[3:] and d.update(session_id=sys.argv[3]); print(json.dumps(d, ensure_ascii=False))' "$USER_ID" "$@") || return 1
    curl --fail --silent --show-error -N -X POST "$BASE/chat/stream" -H 'Content-Type: application/json' -d "$body"
}

# sse_get <SSE 原文> <python 表达式>：events 为 [(name, data)]
sse_get() {
    printf '%s\n' "$1" | python3 -c '
import json, sys
events, name = [], None
for line in sys.stdin.read().splitlines():
    if line.startswith("event: "): name = line[7:]
    elif line.startswith("data: "): events.append((name, json.loads(line[6:])))
print(eval(sys.argv[1]))' "$2"
}

fail() { echo "❌ $1"; exit 1; }

echo '=== [1/4] 多轮指代消解和意图评估 ==='
uv run python evals/run_multiturn_eval.py || fail '[1/4] 多轮评估失败'
echo '✅ [1/4] 多轮评估通过'

echo '=== [2/4] 意图准确率、解析率和「其他」召回评估 ==='
uv run python evals/run_intent_eval.py || fail '[2/4] 意图评估失败'
echo '✅ [2/4] 意图评估通过'

echo '=== [3/4] 同一会话指代消解和售后路由 ==='
sse=$(ask '订单 1001 到哪了') || fail '[3/4] 第 1 轮请求失败'
cid=$(sse_get "$sse" 'next(d["session_id"] for n, d in events if n == "session")') || fail '[3/4] 第 1 轮没有有效 session_id'
finished=$(sse_get "$sse" 'bool(events) and events[-1] == ("done", {"finish_reason": "stop"})') || fail '[3/4] 第 1 轮 SSE 解析失败'
[[ "$finished" == True ]] || fail '[3/4] 第 1 轮没有正常结束'
sse=$(ask '这个能退吗' "$cid") || fail '[3/4] 第 2 轮请求失败'
understood=$(sse_get "$sse" 'next(d for n, d in events if n == "understood")') || fail '[3/4] 第 2 轮没有有效 understood 事件'
echo "理解结果：$understood"
matched=$(sse_get "$sse" 'any(n == "understood" and any(order_id in d.get("resolved_input", "") for order_id in ("1004", "1001")) and d.get("intent") == "退款退货" for n, d in events)') || fail '[3/4] 第 2 轮 SSE 解析失败'
[[ "$matched" == True ]] || fail '[3/4] resolved_input 未包含 1004 或 1001，或 intent 不是退款退货'
line=$(grep "turn conversation=$cid " "$LOG" | tail -1) || fail "[3/4] 日志中没有 turn conversation=$cid"
echo "日志：$line"
python3 -c '
import sys
_, marker, trace = sys.argv[1].partition("trace=")
if not marker:
    sys.exit(1)
trace = trace.split(" gate=", 1)[0]
position = 0
for node in ("resolve_reference", "classify_intent", "ensure_order", "fetch_order", "expand_query", "retrieve_multi", "confidence_gate"):
    found = trace.find(node, position)
    if found < 0:
        sys.exit(1)
    position = found + len(node)
' "$line" || fail '[3/4] trace 缺少要求的节点或节点顺序不正确'
echo '✅ [3/4] 同一会话指代消解和售后路由通过'

echo '=== [4/4] 订单选择中断和恢复 ==='
sse=$(ask '我要退货') || fail '[4/4] 退货请求失败'
cid=$(sse_get "$sse" 'next(d["session_id"] for n, d in events if n == "session")') || fail '[4/4] 没有有效 session_id'
interrupted=$(sse_get "$sse" 'any(n == "order_picker" for n, d in events) and bool(events) and events[-1] == ("done", {"finish_reason": "interrupted"})') || fail '[4/4] 订单选择 SSE 解析失败'
[[ "$interrupted" == True ]] || fail '[4/4] 没有 order_picker 或最后不是 done interrupted'
order_id=$(sse_get "$sse" 'next(d for n, d in events if n == "order_picker")["orders"][0]["order_id"]') || fail '[4/4] 无法取得第 1 张订单卡片的 order_id'
body=$(python3 -c 'import json,sys; print(json.dumps({"session_id":sys.argv[1],"user_id":sys.argv[2],"order_id":sys.argv[3]}, ensure_ascii=False))' "$cid" "$USER_ID" "$order_id") || fail '[4/4] resume 请求体生成失败'
sse=$(curl --fail --silent --show-error -N -X POST "$BASE/chat/resume" -H 'Content-Type: application/json' -d "$body") || fail '[4/4] resume 请求失败'
resumed=$(sse_get "$sse" 'any(n == "token" for n, d in events) and bool(events) and events[-1] == ("done", {"finish_reason": "stop"})') || fail '[4/4] resume SSE 解析失败'
[[ "$resumed" == True ]] || fail '[4/4] resume 没有 token 事件或最后不是 done stop'
echo "选中订单：$order_id"
echo '✅ [4/4] 订单选择中断和恢复通过'
echo '浏览器点选卡片的验证请人工完成（Task 10 检查步骤 2）'

echo '全部验收通过。'
