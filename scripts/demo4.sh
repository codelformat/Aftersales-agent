#!/usr/bin/env bash
# ch04 验收。前置：MySQL、Milvus 已启动，已执行 build_kb.py --rebuild，服务已启动。
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
BASE="${BASE_URL:-http://127.0.0.1:8000}"

if ! curl --fail --silent --show-error --connect-timeout 5 --max-time 10 "$BASE/health" >/dev/null 2>&1; then
    printf '%s\n' '请先启动服务：uv run uvicorn app.main:app --port 8000' >&2
    exit 1
fi

mysql_q() {
    docker exec aftersales-mysql mysql -N --default-character-set=utf8mb4 \
        -uaftersales -paftersales aftersales -e "$1"
}

ask() {
    curl --fail --silent --show-error -N -X POST "$BASE/chat/stream" \
        -H 'Content-Type: application/json' \
        -d "{\"user_id\":\"demo4-$(date +%s)-$RANDOM\",\"message\":\"$1\"}"
}

printf '%s\n' '=== 验收 1：四策略对比报告 ==='
uv run python evals/run_rag_eval.py --stage retrieval

printf '%s\n' '=== 验收 2：BM25 单路命中型号 ==='
uv run python - <<'PY'
import asyncio
import sys

from app.db.engine import dispose_engine
from app.knowledge.milvus import close_milvus, ensure_collection
from app.knowledge.rerank import close_rerank
from app.knowledge.retrieval import retrieve


async def main() -> int:
    try:
        await ensure_collection()
        r = await retrieve("X3 Pro 续航多久", "bm25")
        top = r.ranked[:3]
        for i, e in enumerate(top, 1):
            print(f"{i}. {e.section_path} | BM25 {e.score:.3f}")
        return 0 if any("X3 Pro" in e.section_path for e in top) else 1
    finally:
        await close_milvus()
        await close_rerank()
        await dispose_engine()


sys.exit(asyncio.run(main()))
PY

printf '%s\n' '=== 验收 3：引用编号定位原文 ==='
sse="$(ask 'X3 Pro 耳机充满电能用多久？')"
read -r chunk_id section_path < <(printf '%s\n' "$sse" | uv run python -c '
import json, re, sys
events, name = [], None
for line in sys.stdin.read().splitlines():
    if line.startswith("event: "):
        name = line[7:]
    elif line.startswith("data: "):
        events.append((name, json.loads(line[6:])))
reply = "".join(d["text"] for n, d in events if n == "token")
cites = next((d for n, d in events if n == "citations"), None)
print("回复：" + reply, file=sys.stderr)
if cites is None or cites["refused"] or not cites["items"] or not re.search(r"\[\d+\]", reply):
    print("验收失败：没有引用编号或引用列表", file=sys.stderr)
    sys.exit(1)
first = cites["items"][0]
print(first["chunk_id"], first["section_path"])
')
chunk_json="$(curl --fail --silent --show-error "$BASE/api/knowledge/chunks/$chunk_id")"
CHUNK_JSON="$chunk_json" EXPECTED="$section_path" uv run python -c '
import json, os, sys
got = json.loads(os.environ["CHUNK_JSON"])["section_path"]
print("接口返回章节路径：" + got)
sys.exit(0 if got == os.environ["EXPECTED"] else 1)
'
printf '%s\n' '页面部分：在聊天页点击角标，人工确认显示原文和章节路径。'

printf '%s\n' '=== 验收 4：拒答并进入低置信度池 ==='
before="$(mysql_q 'SELECT COUNT(*) FROM low_confidence_questions')"
sse="$(ask '你们的 X9 耳机支持无线充电吗？')"
printf '%s\n' "$sse" | uv run python -c '
import json, sys
from app.prompts import REFUSAL_PREFIX
events, name = [], None
for line in sys.stdin.read().splitlines():
    if line.startswith("event: "):
        name = line[7:]
    elif line.startswith("data: "):
        events.append((name, json.loads(line[6:])))
reply = "".join(d["text"] for n, d in events if n == "token")
cites = next((d for n, d in events if n == "citations"), None)
print("回复：" + reply)
ok = cites is not None and cites["refused"] and reply.strip().startswith(REFUSAL_PREFIX)
sys.exit(0 if ok else 1)
'
after="$(mysql_q 'SELECT COUNT(*) FROM low_confidence_questions')"
if [[ "$after" -ne $((before + 1)) ]]; then
    printf '%s\n' "验收失败：问题池行数 $before → $after" >&2
    exit 1
fi
mysql_q 'SELECT id, raw_question, source, reason FROM low_confidence_questions ORDER BY id DESC LIMIT 1'
printf '%s\n' 'ch04 验收通过'
