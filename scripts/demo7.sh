#!/usr/bin/env bash
# ch07 验收。前置：MySQL、Milvus 已启动，已 build_kb，端口 8000 空闲。脚本自己按配置启停服务。
# 用法：bash scripts/demo7.sh <日志目录>
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
DIR="${1:?用法：bash scripts/demo7.sh <日志目录>}"
mkdir -p "$DIR" log
APP_LOG=log/app.log
touch "$APP_LOG"
DEMO_ENV=(MODEL_CONTEXT_WINDOW=18000 MAX_OUTPUT_TOKENS=2000 MAX_USER_INPUT_TOKENS=2000 MAX_AGENT_STEPS=3
          TOOL_RESULT_MAX_TOKENS=1200 RERANK_TOP_K=5)
PID=""

if pgrep -f "uvicorn app.main:app" >/dev/null; then
    echo '端口 8000 上已有服务在运行，请先停止。' >&2
    exit 1
fi

stop() {
    [[ -n "$PID" ]] || return 0
    kill "$PID" 2>/dev/null || true
    wait "$PID" 2>/dev/null || true
    PID=""
    for _ in $(seq 20); do pgrep -f "uvicorn app.main:app" >/dev/null || return 0; sleep 0.5; done
    echo '旧服务进程没有退出' >&2
    return 1
}
fail() { echo "❌ $1"; stop || true; exit 1; }
trap 'rc=$?; stop || true; exit $rc' EXIT

# start <名称> [环境变量...]：记下 app.log 当前长度，启动服务，等 /health 最多 30 秒。
start() {
    local name=$1
    shift
    OFFSET=$(wc -c < "$APP_LOG")
    env "$@" uv run uvicorn app.main:app --port 8000 > "$DIR/$name.out" 2>&1 &
    PID=$!
    for _ in $(seq 60); do
        curl --fail --silent http://127.0.0.1:8000/health >/dev/null 2>&1 && return 0
        sleep 0.5
    done
    fail "服务启动超时（${name}），见 $DIR/$name.out"
}
# section：本次启动后新增的 app.log 内容。
section() { tail -c +"$((OFFSET + 1))" "$APP_LOG"; }
count() { section | grep -c "$1" || true; }

echo '=== [1/4] 默认配置 22 轮：不降级、不摘要 ==='
start default
uv run python scripts/demo7_chat.py --user "demo7-a-$RANDOM" --dialog scripts/demo7_dialog.json \
    || fail '[1/4] 有轮次没有正常结束'
sleep 2
section > "$DIR/default.log"
stop
if grep -q '层1 降级\|summary trigger' "$DIR/default.log"; then fail '[1/4] 默认配置出现了降级或摘要'; fi
echo '✅ [1/4] 默认配置 22 轮没有降级和摘要'

echo '=== [2/4] 只改窗口：上下文预算不足 ==='
start window-only MODEL_CONTEXT_WINDOW=18000
section > "$DIR/window-only.log"
stop
grep -q '上下文预算不足' "$DIR/window-only.log" || fail '[2/4] 没有报上下文预算不足'
echo '✅ [2/4] 只改窗口时报上下文预算不足'

echo '=== [3/4] 演示配置：完整级联，靠梗概答对最早的订单 ==='
start demo "${DEMO_ENV[@]}"
section | grep -q 'history=5650 layer1=3954 layer2=1695' || fail '[3/4] 预算不是 5650/3954/1695'
USER_C="demo7-c-$RANDOM"
out=$(uv run python scripts/demo7_chat.py --user "$USER_C" --dialog scripts/demo7_dialog.json) \
    || fail '[3/4] 有轮次没有正常结束'
sid=$(echo "$out" | tail -1 | python3 -c 'import json,sys; print(json.load(sys.stdin)["session_id"])')
uv run python scripts/demo7_chat.py --user "$USER_C" --session "$sid" --dialog scripts/demo7_dialog_more.json \
    || fail '[3/4] 续聊有轮次没有正常结束'
# 等后台摘要结束：最多 60 秒，直到 trigger 数等于 done 数加 fail 数。
for _ in $(seq 60); do
    [[ $(count 'summary trigger') -eq $(( $(count 'summary done') + $(count 'summary fail') )) ]] && break
    sleep 1
done
ans=$(uv run python scripts/demo7_chat.py --user "$USER_C" --session "$sid" --ask '最开始那个订单后来怎么说') \
    || fail '[3/4] 最后一问没有正常结束'
section > "$DIR/demo.log"
stop
grep -q '层1 降级' "$DIR/demo.log" || fail '[3/4] 没有层1 降级'
grep -q 'summary trigger' "$DIR/demo.log" || fail '[3/4] 没有 summary trigger'
grep -q 'summary done conversation=.* 第1段' "$DIR/demo.log" || fail '[3/4] 没有 summary done 第1段'
# 不阻塞：第一条 summary trigger 前面已有本轮的 turn 行；summary done 在 trigger 之后。
python3 - "$DIR/demo.log" <<'EOF' || fail '[3/4] 摘要顺序不对'
import sys
lines = open(sys.argv[1], encoding="utf-8").read().splitlines()
t = next(i for i, l in enumerate(lines) if "summary trigger" in l)
d = next(i for i, l in enumerate(lines) if "summary done" in l)
assert any(" turn conversation=" in l for l in lines[:t]), "trigger 之前没有 turn 行"
assert d > t, "done 早于 trigger"
print(lines[t]); print(lines[d])
EOF
echo "$ans" | tail -1 | python3 -c 'import json,sys; r=json.load(sys.stdin)["last_reply"]; print(r); sys.exit(0 if "1001" in r else 1)' \
    || fail '[3/4] 回复里没有最早的订单号 1001'
echo '✅ [3/4] 降级、摘要级联完整，靠梗概答对了最早的订单'

echo '=== [4/4] model_ctx / history_ctx ==='
for k in model_ctx history_ctx; do
    n=$(grep -c "$k" "$DIR/demo.log" || true)
    [[ "$n" -gt 0 ]] || fail "[4/4] 没有 $k"
    echo "${k}：${n} 条，最后一条："
    grep -A8 "$k" "$DIR/demo.log" | tail -9
done
echo '✅ [4/4] 每轮发给模型的摘要和滑窗都在日志中'
