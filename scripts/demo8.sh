#!/usr/bin/env bash
# ch08 验收（6 项）。前置：MySQL 已启动，端口 8000、8101、8102 空闲。脚本自己启停两个 MCP Server 和客服服务。
# 用法：bash scripts/demo8.sh <日志目录>
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
DIR="${1:?用法：bash scripts/demo8.sh <日志目录>}"
mkdir -p "$DIR"
PY=.venv/bin/python
POLICY=config/tools.json
POINTS_DST=app/tools/builtin/query_member_points.py
REPAIR_DST=mcp_servers/aftersales/tools/query_repair_progress.py
APP_PID=""
LOG_PID=""
AFT_PID=""
BACKUP_DONE=""

port_open() { (exec 3<>/dev/tcp/127.0.0.1/"$1") 2>/dev/null; }

# kill_wait <PID> <端口>：停掉进程，再等端口释放最多 15 秒。
kill_wait() {
    [[ -n "$1" ]] || return 0
    kill "$1" 2>/dev/null || true
    wait "$1" 2>/dev/null || true
    for _ in $(seq 30); do port_open "$2" || return 0; sleep 0.5; done
    echo "端口 $2 没有释放" >&2
    return 1
}
stop_app() { kill_wait "$APP_PID" 8000 || true; APP_PID=""; }
stop_logistics() { kill_wait "$LOG_PID" 8101 || true; LOG_PID=""; }
stop_aftersales() { kill_wait "$AFT_PID" 8102 || true; AFT_PID=""; }

cleanup() {
    stop_app
    stop_logistics
    stop_aftersales
    rm -f "$POINTS_DST" "$REPAIR_DST"
    [[ -z "$BACKUP_DONE" ]] || cp "$DIR/tools.json.bak" "$POLICY"
}
trap 'rc=$?; cleanup || true; exit $rc' EXIT
fail() { echo "❌ $1"; exit 1; }

for p in 8000 8101 8102; do
    if port_open "$p"; then
        echo "端口 $p 上已有进程在运行，请先停止。" >&2
        exit 1
    fi
done

cp "$POLICY" "$DIR/tools.json.bak"
BACKUP_DONE=1

# wait_port <端口> <名称> <日志>：等端口可连最多 30 秒。
wait_port() {
    for _ in $(seq 60); do port_open "$1" && return 0; sleep 0.5; done
    fail "$2 启动超时，见 $3"
}
# start_logistics [环境变量...]、start_aftersales：直接起 Python 进程，PID 即真实服务。
start_logistics() {
    env DEMO8=1 "$@" $PY -m mcp_servers.logistics --port 8101 > "$DIR/logistics.out" 2>&1 &
    LOG_PID=$!
    wait_port 8101 logistics "$DIR/logistics.out"
}
start_aftersales() {
    env DEMO8=1 $PY -m mcp_servers.aftersales --port 8102 > "$DIR/aftersales.out" 2>&1 &
    AFT_PID=$!
    wait_port 8102 aftersales "$DIR/aftersales.out"
}
# start_app <名称>：启动客服服务，等 /health 最多 30 秒。
start_app() {
    $PY -m uvicorn app.main:app --port 8000 > "$DIR/app-$1.out" 2>&1 &
    APP_PID=$!
    for _ in $(seq 60); do
        curl --noproxy '*' --fail --silent http://127.0.0.1:8000/health >/dev/null 2>&1 && return 0
        sleep 0.5
    done
    fail "客服服务启动超时（$1），见 $DIR/app-$1.out"
}

mysql_q() {
    docker exec aftersales-mysql mysql --default-character-set=utf8mb4 -N -uaftersales -paftersales aftersales -e "$1"
}
jget() { printf '%s' "$1" | python3 -c 'import json,sys; v=json.load(sys.stdin)[sys.argv[1]]; print(str(v).lower() if isinstance(v, bool) else v)' "$2"; }
# policy <python 语句>：用变量 d 改写策略文件（先从备份还原，再应用）。
policy() {
    cp "$DIR/tools.json.bak" "$POLICY"
    python3 - "$POLICY" "$1" <<'PYEOF'
import json, sys
path, code = sys.argv[1], sys.argv[2]
d = json.load(open(path, encoding="utf-8"))
exec(code)
json.dump(d, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
PYEOF
}
# audit <会话ID>：本次运行新增的审计行（制表符分隔：工具、来源、Server、状态、原因、重试、耗时）。
audit() {
    mysql_q "SELECT tool_name, tool_source, IFNULL(mcp_server,''), status, IFNULL(error_message,''), retry_count, IFNULL(duration_ms,'NULL') FROM tool_audit_logs WHERE id > $AUDIT0 AND conversation_id = $1 ORDER BY id"
}
tickets_of() { mysql_q "SELECT ticket_no FROM tickets WHERE conversation_id = $1"; }
# chat <条目号> <用户> <会话ID或空> <消息>：打印摘要 JSON。
chat() {
    local item=$1 user=$2 sid=$3 msg=$4
    $PY scripts/demo8_chat.py --user "$user" ${sid:+--session "$sid"} --message "$msg" --out "$DIR/item$item.jsonl"
}
resume() {
    $PY scripts/demo8_chat.py --user "$1" --session "$2" --confirm "$3" --out "$DIR/item$4.jsonl"
}

AUDIT0=$(mysql_q "SELECT COALESCE(MAX(id), 0) FROM tool_audit_logs")
RUN="$RANDOM"
rm -f "$DIR"/item*.jsonl

start_logistics
start_aftersales

echo '=== [1/6] 内置演示工具：放进目录，重启，即可使用 ==='
cp scripts/demo8_assets/query_member_points.py "$POINTS_DST"
start_app points
USER1="demo8-1-$RUN"
out=$(chat 1 "$USER1" "" '我的会员手机号是 13800001234，帮我查一下我有多少积分') || fail '[1/6] 对话没有正常结束'
sid=$(jget "$out" session_id)
rows=$(audit "$sid")
echo "$rows"
printf '%s\n' "$rows" | grep -q "^query_member_points	builtin		成功	" || fail '[1/6] 审计里没有 query_member_points 成功记录'
points=$($PY -c 'import random; print(random.Random("points:13800001234").randint(100, 5000))')
jget "$out" reply | grep -q "$points" || fail "[1/6] 回复里没有积分 $points"
stop_app
rm -f "$POINTS_DST"
echo "✅ [1/6] query_member_points 调用成功，回复含积分 $points"

echo '=== [2/6] MCP 工具：物流轨迹 ==='
start_app mcp
USER2="demo8-2-$RUN"
out=$(chat 2 "$USER2" "" '帮我查一下订单 1001 的物流轨迹') || fail '[2/6] 对话没有正常结束'
sid=$(jget "$out" session_id)
rows=$(audit "$sid")
echo "$rows"
printf '%s\n' "$rows" | grep -q "^query_logistics	mcp	logistics	成功	" || fail '[2/6] 审计里没有 query_logistics 的 mcp 成功记录'
jget "$out" reply | grep -q '运输\|在途\|派送\|揽收\|快件' || fail '[2/6] 回复里没有轨迹状态'
echo '✅ [2/6] 物流经 MCP logistics 查询成功，回复含轨迹状态'

echo '=== [3/6] 热加载：新增 MCP 工具，客服服务不重启 ==='
pid_before=$APP_PID
cp scripts/demo8_assets/query_repair_progress.py "$REPAIR_DST"
policy 'd["servers"]["aftersales"]["tools"]["query_repair_progress"] = "read"'
stop_aftersales
start_aftersales
USER3="demo8-3-$RUN"
out=$(chat 3 "$USER3" "" '帮我查一下订单 1001 的维修进度') || fail '[3/6] 对话没有正常结束'
sid=$(jget "$out" session_id)
rows=$(audit "$sid")
echo "$rows"
printf '%s\n' "$rows" | grep -q "^query_repair_progress	mcp	aftersales	成功	" || fail '[3/6] 审计里没有 query_repair_progress 成功记录'
[[ "$pid_before" == "$APP_PID" ]] && kill -0 "$APP_PID" 2>/dev/null || fail '[3/6] 客服服务 PID 变了'
rm -f "$REPAIR_DST"
cp "$DIR/tools.json.bak" "$POLICY"
stop_aftersales
start_aftersales
echo "✅ [3/6] 新工具调用成功，客服服务 PID $APP_PID 不变"

echo '=== [4/6] 写工具：先追问，预览，确认后建单 ==='
USER4="demo8-4-$RUN"
out=$(chat 4 "$USER4" "" '帮我建个工单') || fail '[4/6] 第 1 句没有正常结束'
sid=$(jget "$out" session_id)
[[ $(jget "$out" ticket_preview) == false ]] || fail '[4/6] 描述不全时就出了预览'
echo "追问：$(jget "$out" reply)"
out=$(chat 4 "$USER4" "$sid" '蓝牙耳机左耳没声音，买了一周') || fail '[4/6] 第 2 句没有正常结束'
[[ $(jget "$out" ticket_preview) == true ]] || fail '[4/6] 没有收到 ticket_preview'
out=$(resume "$USER4" "$sid" true 4) || fail '[4/6] 确认后没有正常结束'
no=$(tickets_of "$sid")
[[ -n "$no" && $(printf '%s\n' "$no" | wc -l) -eq 1 ]] || fail '[4/6] tickets 表没有这一条'
jget "$out" reply | grep -q "$no" || fail "[4/6] 回复里没有工单号 $no"
echo '✅ [4/6] 追问、预览、确认后建单，回复含工单号 '"$no"

echo '=== [5/6] 取消：不建单，审计记权限拒绝 ==='
USER5="demo8-5-$RUN"
out=$(chat 5 "$USER5" "" '帮我建个工单') || fail '[5/6] 第 1 句没有正常结束'
sid=$(jget "$out" session_id)
out=$(chat 5 "$USER5" "$sid" '蓝牙耳机左耳没声音，买了一周') || fail '[5/6] 第 2 句没有正常结束'
[[ $(jget "$out" ticket_preview) == true ]] || fail '[5/6] 没有收到 ticket_preview'
resume "$USER5" "$sid" false 5 >/dev/null || fail '[5/6] 取消后没有正常结束'
[[ -z "$(tickets_of "$sid")" ]] || fail '[5/6] 取消后 tickets 表仍有新增'
rows=$(audit "$sid")
echo "$rows"
printf '%s\n' "$rows" | grep -q "^create_ticket	builtin		权限拒绝	.*用户取消" || fail '[5/6] 审计里没有 create_ticket 权限拒绝（用户取消）'
echo '✅ [5/6] 取消后没有新增工单，审计记权限拒绝（用户取消）'

echo '=== [6/6] 超时：读工具重试 2 次，写工具不重试 ==='
stop_logistics
start_logistics MOCK_DELAY_SECONDS=5
policy 'd["overrides"]["query_logistics"] = {"timeout_seconds": 2}'
USER6="demo8-6-$RUN"
out=$(chat 6 "$USER6" "" '帮我查一下订单 1001 的物流轨迹') || fail '[6/6] 读超时对话没有正常结束'
sid=$(jget "$out" session_id)
rows=$(audit "$sid")
echo "$rows"
printf '%s\n' "$rows" | grep -q "^query_logistics	mcp	logistics	超时	.*	2	[0-9]" || fail '[6/6] 审计不是 超时、retry_count=2、duration_ms 有值'
[[ -n "$(jget "$out" reply)" ]] || fail '[6/6] 读超时后回复为空'
echo "读超时回复：$(jget "$out" reply)"
stop_logistics
start_logistics
policy 'd["overrides"]["create_ticket"] = {"timeout_seconds": 0.001}'
out=$(chat 6 "$USER6" "" '帮我建个工单') || fail '[6/6] 写超时第 1 句没有正常结束'
sid=$(jget "$out" session_id)
out=$(chat 6 "$USER6" "$sid" '蓝牙耳机左耳没声音，买了一周') || fail '[6/6] 写超时第 2 句没有正常结束'
[[ $(jget "$out" ticket_preview) == true ]] || fail '[6/6] 没有收到 ticket_preview'
out=$(resume "$USER6" "$sid" true 6) || fail '[6/6] 写超时确认后没有正常结束'
rows=$(audit "$sid")
echo "$rows"
printf '%s\n' "$rows" | grep -q "^create_ticket	builtin		超时	.*	0	" || fail '[6/6] 审计不是 create_ticket 超时、retry_count=0'
jget "$out" reply | grep -q '超时' || fail '[6/6] 回复不是超时话术'
cp "$DIR/tools.json.bak" "$POLICY"
echo '✅ [6/6] 读超时重试 2 次后如实回复，写超时不重试并给超时话术'
