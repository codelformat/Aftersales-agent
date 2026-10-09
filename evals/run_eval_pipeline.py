"""自动化评估流水线：生产策略 hybrid_rerank 跑全量评估集，每轮写一行 eval_runs。

用法：
  uv run python evals/run_eval_pipeline.py --trigger 手动
  uv run python evals/run_eval_pipeline.py --trend [--last 10]
crontab 示例（每天 03:00）：
  0 3 * * * cd /path/to/Aftersales-agent && uv run python evals/run_eval_pipeline.py --trigger 定时 >> log/eval_pipeline.log 2>&1
"""

import argparse
import asyncio
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent))

from app.db.engine import dispose_engine, get_sessionmaker
from app.knowledge.milvus import close_milvus, ensure_collection
from app.knowledge.rerank import close_rerank
from app.repositories import eval_runs
from evals.eval_trend import RunPoint, build_metrics, render_trend, trend
from evals.rag_eval_set import known_source_keys, load_samples, validate
from evals.run_rag_eval import collect

STRATEGIES = ("hybrid_rerank",)


def whole_stage_failed(*, scores, results) -> bool:
    return not scores or not results


async def save_run(triggered_by: str, dataset_size: int, metrics: dict) -> int:
    async with get_sessionmaker()() as s:
        row = await eval_runs.add(s, triggered_by=triggered_by, dataset_size=dataset_size, metrics=metrics)
        await s.commit()
        return row.id


async def load_points(limit: int) -> list[RunPoint]:
    async with get_sessionmaker()() as s:
        rows = await eval_runs.list_recent(s, limit)
    return [RunPoint(r.id, r.created_at, r.triggered_by, r.dataset_size, r.metrics) for r in rows]


async def run_once(trigger: str, concurrency: int, limit: int | None) -> int:
    try:
        await ensure_collection()
        samples = load_samples()
        errors = validate(samples, set(await known_source_keys()), full=True)
        if errors:
            print("\n".join(errors))
            return 1
        if limit is not None:
            samples = samples[:limit]
        got = await collect(samples, strategies=STRATEGIES, gen_strategies=STRATEGIES, do_retrieval=True,
                            do_generation=True, concurrency=concurrency, write_cases=True)
        if whole_stage_failed(scores=got.scores, results=got.results):
            print("检索段或生成段整体失败，不写 eval_runs")
            return 1
        metrics = build_metrics(got.scores, got.results, len(got.failures))
        run_id = await save_run(trigger, len(samples), metrics)
        print(f"eval_runs 新增第 {run_id} 轮：{metrics}")
        print(render_trend(trend(await load_points(50))))
        return 1 if got.failures else 0
    finally:
        try:
            await close_milvus()
        finally:
            try:
                await close_rerank()
            finally:
                await dispose_engine()


async def show_trend(last: int) -> int:
    try:
        print(render_trend(trend(await load_points(max(last * 5, 50)), last=last)))
        return 0
    finally:
        await dispose_engine()


def main() -> int:
    parser = argparse.ArgumentParser(description="自动化评估流水线。正常运行会调用上游模型。")
    parser.add_argument("--trigger", choices=("定时", "手动"), default="手动")
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--limit", type=int, help="只用于调试")
    parser.add_argument("--trend", action="store_true")
    parser.add_argument("--last", type=int, default=10)
    args = parser.parse_args()
    if args.trend:
        return asyncio.run(show_trend(args.last))
    return asyncio.run(run_once(args.trigger, args.concurrency, args.limit))


if __name__ == "__main__":
    sys.exit(main())

