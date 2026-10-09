"""置信度闸参数校准。对评估集每题调一次生产检索，缓存信号，再离线网格搜索。

用法：
  uv run python evals/run_gate_calibration.py                 # 调上游采集信号并搜索
  uv run python evals/run_gate_calibration.py --from-cache <jsonl>   # 只用缓存搜索
"""

import argparse
import asyncio
import json
import logging
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from time import perf_counter

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent))

from app.db.engine import dispose_engine
from app.knowledge.milvus import close_milvus, ensure_collection
from app.knowledge.query import understand
from app.knowledge.rerank import close_rerank
from app.knowledge.retrieval import retrieve, source_key
from evals.gate_calibration import Params, SignalRow, cross_validate, evaluate, render_report, search
from evals.rag_eval_set import load_samples

logger = logging.getLogger(__name__)
REPORTS_DIR = SCRIPT_DIR / "reports"


async def collect(concurrency: int) -> list[SignalRow]:
    sem = asyncio.Semaphore(concurrency)
    rows: list[SignalRow] = []
    samples = load_samples()

    async def one(sample):
        async with sem:
            try:
                # 与 ch04 门槛扫描一致，使用 exclude_mined 过滤。
                r = await retrieve(sample.query, plan=await understand(sample.query), exclude_mined=True)
            except Exception:
                logger.exception("检索失败：%s", sample.id)
                return
            ranked = [source_key(e.section_path, e.question) for e in r.ranked]
            relevant = bool(ranked) and any(ranked[0] in g for g in sample.relevant)
            rows.append(SignalRow(sample.id, sample.bucket, relevant, [e.score for e in r.ranked[:5]]))
            if len(rows) % 25 == 0:
                print(f"信号采集：{len(rows)}/{len(samples)}", flush=True)

    try:
        await ensure_collection()
        await asyncio.gather(*(one(s) for s in samples))
    finally:
        try:
            await close_milvus()
        finally:
            try:
                await close_rerank()
            finally:
                await dispose_engine()
    return sorted(rows, key=lambda r: r.sample_id)


def main() -> int:
    parser = argparse.ArgumentParser(description="置信度闸参数校准。")
    parser.add_argument("--from-cache", type=Path)
    parser.add_argument("--concurrency", type=int, default=3)
    args = parser.parse_args()
    if args.concurrency < 1:
        parser.error("--concurrency 必须大于 0")
    stamp = f"{datetime.now():%Y%m%d-%H%M%S}"
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    if args.from_cache:
        rows = [SignalRow(**json.loads(line)) for line in args.from_cache.read_text(encoding="utf-8").splitlines()]
    else:
        rows = asyncio.run(collect(args.concurrency))
        cache = REPORTS_DIR / f"gate_signals_{stamp}.jsonl"
        cache.write_text("\n".join(json.dumps(asdict(r), ensure_ascii=False) for r in rows), encoding="utf-8")
        print(f"信号缓存：{cache}")
    started = perf_counter()
    full = search(rows)
    search_seconds = perf_counter() - started
    folds = cross_validate(rows)
    total_seconds = perf_counter() - started
    report = render_report(full, folds, evaluate(rows, Params((1.0, 0.0, 0.0), 3, 0.20)), len(rows))
    report += (f"\n\n## 搜索耗时\n\n- 全量网格搜索：{search_seconds:.3f} 秒"
               f"\n- 全量搜索与 5 折交叉验证：{total_seconds:.3f} 秒\n")
    path = REPORTS_DIR / f"gate_calibration_{stamp}.md"
    path.write_text(report, encoding="utf-8")
    print(report)
    print(f"报告：{path}")
    return 0 if full is not None else 1


if __name__ == "__main__":
    sys.exit(main())
