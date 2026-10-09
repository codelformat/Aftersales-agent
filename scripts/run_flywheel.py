"""补跑飞轮：处理所有 matched_review_id 为空的落池问题。

用法：
  uv run python scripts/run_flywheel.py            # 全部处理
  uv run python scripts/run_flywheel.py --limit 20
  uv run python scripts/run_flywheel.py --status   # 只看统计
crontab 示例（每 30 分钟补跑一次）：
  */30 * * * * cd /path/to/Aftersales-agent && uv run python scripts/run_flywheel.py >> log/flywheel.log 2>&1
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.engine import dispose_engine, get_sessionmaker
from app.flywheel.pipeline import process
from app.repositories import low_confidence, review_queue


async def run(limit: int | None, status_only: bool) -> int:
    try:
        async with get_sessionmaker()() as s:
            pending = await low_confidence.count_unmatched(s)
            waiting = len(await review_queue.list_items(s, "待审"))
            ids = await low_confidence.list_unmatched_ids(s, limit)
        print(f"待处理落池问题：{pending}；待审缺口：{waiting}")
        if status_only:
            return 0
        failed = 0
        for lcq_id in ids:
            if await process(lcq_id) is None:
                failed += 1
        print(f"本次处理：{len(ids)}；失败：{failed}")
        return 1 if failed else 0
    finally:
        await dispose_engine()


def main() -> int:
    parser = argparse.ArgumentParser(description="补跑飞轮流水线。")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args()
    return asyncio.run(run(args.limit, args.status))


if __name__ == "__main__":
    sys.exit(main())
