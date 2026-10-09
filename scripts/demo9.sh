#!/usr/bin/env bash
# ch09 六项验收。用法：bash scripts/demo9.sh <日志目录>
# 前置：先 docker compose up -d --wait；
# 再 docker compose -f docker-compose.langfuse.yml up -d --wait；再运行 build_kb。
# 端口 8000、8101、8102 须空闲。脚本自己启停两个 MCP Server 和服务。
# C8 洗碗机问题已有知识。改问 L2 小爱同学控制，核准前知识库没有答案。
# 可重复运行：启动前会清理上次演示含“小爱同学”的飞轮块及其向量，恢复知识缺口。
# review_queue 和 low_confidence_questions 的历史行保留。
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
DIR="${1:?用法：bash scripts/demo9.sh <日志目录>}"
mkdir -p "$DIR"
DIR=$(cd "$DIR" && pwd)
PY=.venv/bin/python
APP_PID=""
LOG_PID=""
AFT_PID=""
STEP=前置检查
BASE=http://127.0.0.1:8000
QUESTION='L2 台灯可以用小爱同学语音控制吗？'
ANSWER='L2 台灯不支持小爱同学等语音助手控制，请用灯身触控按键调节亮度和色温。'
FEEDBACK_QUESTION='X3 Pro 耳机的续航是多久？'

port_open() { (exec 3<>/dev/tcp/127.0.0.1/"$1") 2>/dev/null; }
fail() { echo "❌ $1"; exit 1; }
# kill_wait <PID> <端口>：最多等 15 秒，再强制停止并回收子进程。
kill_wait() {
    [[ -n "$1" ]] || return 0
    kill "$1" 2>/dev/null || true
    for _ in $(seq 30); do
        kill -0 "$1" 2>/dev/null || break
        sleep 0.5
    done
    kill -KILL "$1" 2>/dev/null || true
    wait "$1" 2>/dev/null || true
    ! port_open "$2"
}
cleanup() {
    local rc=0
    kill_wait "$APP_PID" 8000 || rc=1
    kill_wait "$LOG_PID" 8101 || rc=1
    kill_wait "$AFT_PID" 8102 || rc=1
    if [[ "$rc" == 0 ]]; then
        echo '✅ 清理：本次 uvicorn 和两个 MCP Server 已退出'
    else
        echo '❌ 清理：服务端口没有释放'
    fi
    return "$rc"
}
trap 'rc=$?; cleanup || rc=1; exit "$rc"' EXIT
trap 'echo "❌ $STEP：命令失败（行 $LINENO），见 $DIR"' ERR
trap 'exit 130' INT
trap 'exit 143' TERM

for p in 8000 8101 8102; do
    if port_open "$p"; then fail "前置检查：端口 $p 已占用"; fi
done
# 只读取 URL，不 source .env，也不输出密钥。
LANGFUSE_BASE_URL=$(grep -E '^LANGFUSE_BASE_URL=' .env | tail -n 1 | cut -d= -f2-)
LANGFUSE_BASE_URL=$(printf '%s' "$LANGFUSE_BASE_URL" | "$PY" -c 'import sys; print(sys.stdin.read().strip().strip("\"\047").rstrip("/"))')
[[ -n "$LANGFUSE_BASE_URL" ]] || fail '前置检查：.env 缺少 LANGFUSE_BASE_URL'
"$PY" - > "$DIR/mysql-health.out" 2>&1 <<'PYEOF' || fail "前置检查：MySQL 不可用，见 $DIR/mysql-health.out"
import asyncio
from sqlalchemy import text
from app.db.engine import dispose_engine, get_sessionmaker
async def main():
    try:
        async with get_sessionmaker()() as s:
            assert (await s.execute(text("SELECT 1"))).scalar_one() == 1
    finally:
        await dispose_engine()
asyncio.run(main())
PYEOF
curl --noproxy '*' --fail --silent --show-error --max-time 10 http://127.0.0.1:9091/healthz > "$DIR/milvus-health.out" || fail '前置检查：Milvus 不健康'
curl --noproxy '*' --fail --silent --show-error --max-time 10 "$LANGFUSE_BASE_URL/api/public/health" > "$DIR/langfuse-health.json" || fail '前置检查：Langfuse 不健康'
"$PY" - "$DIR/langfuse-health.json" <<'PYEOF' || fail '前置检查：Langfuse health 不是 OK'
import json, sys
assert json.load(open(sys.argv[1]))["status"] == "OK"
PYEOF
echo '✅ 前置检查：MySQL、Milvus、Langfuse 健康，三个服务端口空闲'

STEP='清理上次演示'
echo "=== $STEP ==="
"$PY" - <<'PYEOF'
import asyncio
from sqlalchemy import select
from app.db.engine import dispose_engine, get_sessionmaker
from app.db.models import KnowledgeChunk
from app.knowledge.milvus import close_milvus, delete_vectors
from app.repositories.knowledge import delete_ids

async def main():
    try:
        async with get_sessionmaker()() as s:
            ids = list(await s.scalars(select(KnowledgeChunk.id).where(
                KnowledgeChunk.content_type == "flywheel",
                KnowledgeChunk.questions.contains("小爱同学"),
            )))
            await delete_vectors(ids)
            # 复用仓库函数：先把 prev/next 指针置 NULL，再删除行。
            await delete_ids(s, ids)
            await s.commit()
        print(f"✅ 清理上次演示：删除 {len(ids)} 条飞轮知识块及其 Milvus 向量")
    finally:
        try:
            await close_milvus()
        finally:
            await dispose_engine()

asyncio.run(main())
PYEOF

wait_port() {
    for _ in $(seq 60); do port_open "$1" && return 0; sleep 0.5; done
    fail "启动：$2 超时，见 $3"
}
"$PY" -m mcp_servers.logistics --port 8101 > "$DIR/logistics.out" 2>&1 &
LOG_PID=$!
wait_port 8101 logistics "$DIR/logistics.out"
"$PY" -m mcp_servers.aftersales --port 8102 > "$DIR/aftersales.out" 2>&1 &
AFT_PID=$!
wait_port 8102 aftersales "$DIR/aftersales.out"
"$PY" -m uvicorn app.main:app --port 8000 > "$DIR/app.out" 2>&1 &
APP_PID=$!
ready=""
for _ in $(seq 60); do
    if curl --noproxy '*' --fail --silent --max-time 2 "$BASE/health" >/dev/null 2>&1; then ready=1; break; fi
    sleep 0.5
done
[[ -n "$ready" ]] || fail "启动：客服服务超时，见 $DIR/app.out"
echo '✅ 启动：两个 MCP Server 和客服服务已就绪'

jget() { "$PY" -c 'import json,sys; print(json.load(open(sys.argv[1]))[sys.argv[2]])' "$1" "$2"; }
# chat <文件前缀> <user_id> <原话>：新会话，保存 SSE 和解析后的摘要。
chat() {
    local prefix=$1 user=$2 question=$3
    "$PY" -c 'import json,sys; print(json.dumps({"user_id":sys.argv[1], "message":sys.argv[2]}, ensure_ascii=False))' "$user" "$question" > "$prefix-request.json"
    curl --noproxy '*' --fail --silent --show-error --max-time 180 -N \
        -H 'Content-Type: application/json' --data-binary "@$prefix-request.json" \
        "$BASE/chat/stream" > "$prefix.sse" || fail "$STEP：SSE 请求失败"
    "$PY" - "$prefix" <<'PYEOF' || fail "$STEP：SSE 未正常结束"
import json, sys
from pathlib import Path
prefix = sys.argv[1]
events, name, data = [], None, []
def flush():
    if data:
        events.append((name, json.loads("\n".join(data))))
for line in Path(prefix + ".sse").read_text().splitlines():
    if not line:
        flush()
        name, data = None, []
    elif line.startswith("event: "):
        name = line[7:]
    elif line.startswith("data: "):
        data.append(line[6:])
flush()
assert events and events[-1][0] == "done", events
done = events[-1][1]
assert done.get("finish_reason") == "stop", done
assert not any(n == "error" for n, _ in events), events
summary = {
    "session_id": next(d["session_id"] for n, d in events if n == "session"),
    "reply": "".join(d.get("text", "") for n, d in events if n == "token"),
    "message_id": done["message_id"],
}
Path(prefix + ".json").write_text(json.dumps(summary, ensure_ascii=False))
print(f'会话 {summary["session_id"]}，回复消息 {summary["message_id"]}：{summary["reply"]}')
PYEOF
}
# poll_review <原话> <详情文件> [反馈 source ID]：检查所有待审行，不用最大 ID。
poll_review() {
    "$PY" - "$BASE" "$1" "$2" "${3:-}" <<'PYEOF'
import json, subprocess, sys, time
base, question, path, feedback_id = sys.argv[1:]
deadline = time.monotonic() + 60
def get(url):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("等待待审队列超过 60 秒")
    raw = subprocess.check_output([
        "curl", "--noproxy", "*", "--fail", "--silent", "--show-error",
        "--max-time", str(min(5, remaining)), url,
    ])
    return json.loads(raw)
while time.monotonic() < deadline:
    rows = get(base + "/api/review-queue?status=%E5%BE%85%E5%AE%A1")
    for row in rows:
        detail = get(f'{base}/api/review-queue/{row["id"]}')
        if detail["review_status"] != "待审":
            continue
        for source in detail["sources"]:
            if source["raw_question"] != question:
                continue
            if feedback_id:
                if source["source"] != "user_feedback" or source["id"] != int(feedback_id):
                    continue
            elif not source["retrieved_chunks"]:
                continue
            with open(path, "w") as f:
                json.dump(detail, f, ensure_ascii=False, indent=2)
            print(f'待审行 {detail["id"]}，来源 {source["id"]}（{source["source"]}）')
            print("标准化问题：" + detail["normalized_question"])
            print("示例答案：" + (detail["ai_suggested_answer"] or "（空）"))
            print("召回片段：" + json.dumps(source["retrieved_chunks"], ensure_ascii=False))
            sys.exit(0)
    time.sleep(min(1, max(0, deadline - time.monotonic())))
sys.exit("等待待审队列超过 60 秒，未找到本次原话的来源")
PYEOF
}

RUN=$("$PY" -c 'import uuid; print(uuid.uuid4().hex[:12])')
STEP='[验收 2] 低置信度问题进入待审队列'
echo "=== $STEP ==="
chat "$DIR/first" "demo9-first-$RUN" "$QUESTION"
"$PY" - "$DIR/first.json" <<'PYEOF' || fail "$STEP：回复不是 GATE_FALLBACK_REPLY"
import json, sys
from app.prompts import GATE_FALLBACK_REPLY
assert json.load(open(sys.argv[1]))["reply"] == GATE_FALLBACK_REPLY
PYEOF
poll_review "$QUESTION" "$DIR/review.json" || fail "$STEP：未找到带召回片段的待审来源"
echo '✅ 验收 2：精确兜底，待审来源含原话和召回片段'

STEP='[验收 3] 核准答案后立即检索'
echo "=== $STEP ==="
review_id=$(jget "$DIR/review.json" id)
"$PY" -c 'import json,sys; print(json.dumps({"approved_answer":sys.argv[1], "product_category":"台灯"}, ensure_ascii=False))' "$ANSWER" > "$DIR/approve-request.json"
curl --noproxy '*' --fail --silent --show-error --max-time 120 \
    -H 'Content-Type: application/json' --data-binary "@$DIR/approve-request.json" \
    "$BASE/api/review-queue/$review_id/approve" > "$DIR/approve.json" || fail "$STEP：核准失败"
echo "核准结果：$(cat "$DIR/approve.json")"
chat "$DIR/approved" "demo9-approved-$RUN" "$QUESTION"
"$PY" - "$DIR/approved.json" <<'PYEOF' || fail "$STEP：回复仍兜底或缺少 小爱/不支持"
import json, sys
from app.prompts import GATE_FALLBACK_REPLY
reply = json.load(open(sys.argv[1]))["reply"]
assert reply != GATE_FALLBACK_REPLY and "小爱" in reply and "不支持" in reply, reply
PYEOF
echo '✅ 验收 3：核准答案已向量化，新会话回复含小爱和不支持'

STEP='[验收 4] 用户反馈进入待审队列'
echo "=== $STEP ==="
USER3="demo9-feedback-$RUN"
chat "$DIR/feedback-chat" "$USER3" "$FEEDBACK_QUESTION"
sid3=$(jget "$DIR/feedback-chat.json" session_id)
message_id=$(jget "$DIR/feedback-chat.json" message_id)
"$PY" -c 'import json,sys; print(json.dumps({"user_id":sys.argv[1], "conversation_id":int(sys.argv[2]), "message_id":int(sys.argv[3]), "rating":"down"}))' "$USER3" "$sid3" "$message_id" > "$DIR/feedback-request.json"
curl --noproxy '*' --fail --silent --show-error --max-time 30 \
    -H 'Content-Type: application/json' --data-binary "@$DIR/feedback-request.json" \
    "$BASE/api/feedback" > "$DIR/feedback.json" || fail "$STEP：反馈失败"
poll_review "$FEEDBACK_QUESTION" "$DIR/feedback-review.json" "$(jget "$DIR/feedback.json" id)" || fail "$STEP：未找到本次 user_feedback 来源"
echo '✅ 验收 4：本次回复的 down 反馈已进入待审队列，来源为 user_feedback'

STEP='[验收 1] Langfuse 会话入口'
echo "=== $STEP ==="
echo "$LANGFUSE_BASE_URL/project/aftersales/sessions"
echo "本次会话 ID：$(jget "$DIR/first.json" session_id)、$(jget "$DIR/approved.json" session_id)、$sid3"
echo '✅ 验收 1：已打印会话入口和三个 ID；请在界面打开并检查 trace 链路'

STEP='[验收 5] 按意图汇总 token'
echo "=== $STEP ==="
sleep 10
uv run python scripts/intent_cost.py --days 1 > "$DIR/intent-cost.out" 2> "$DIR/intent-cost.err" || fail "$STEP：汇总失败，见 $DIR/intent-cost.err"
cat "$DIR/intent-cost.out"
echo '✅ 验收 5：token 汇总命令成功'

STEP='[验收 6] 评估趋势'
echo "=== $STEP ==="
uv run python evals/run_eval_pipeline.py --trend > "$DIR/eval-trend.out" 2> "$DIR/eval-trend.err" || fail "$STEP：趋势命令失败，见 $DIR/eval-trend.err"
cat "$DIR/eval-trend.out"
echo '✅ 验收 6：趋势命令成功；两轮全量评估另行运行'
