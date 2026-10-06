"""定时任务：从指定日期的历史客服对话中挖掘问答对，去重后入库。跑一次就退出。

crontab 示例（每天 02:00 处理前一天）：
0 2 * * * cd /path/to/Aftersales-agent && uv run python scripts/mine_qa.py >> log/mine_qa.log 2>&1
"""

import argparse
import asyncio
from datetime import date, timedelta
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.engine import dispose_engine, get_sessionmaker
from app.knowledge.milvus import close_milvus, ensure_collection
from app.knowledge.mining import run_mining
from app.llm import get_dedup_judge, get_qa_extractor
from app.repositories import staging

logger = logging.getLogger(__name__)


async def run(day: date) -> int:
    try:
        await ensure_collection()
        st = await run_mining(day, get_qa_extractor(), get_dedup_judge())
        print(
            f"日期 {day}：会话 {st.extract.conversations}，跳过 {st.extract.skipped}，"
            f"抽取失败 {st.extract.failed}，抽出 {st.extract.pairs} 对（{st.extract.batches} 批）"
        )
        print(
            f"去重：保留 {st.dedup.kept}，丢弃 {st.dedup.discarded}，"
            f"裁定失败 {st.dedup.failed}；向量化 {st.vectorized} 块"
        )
        async with get_sessionmaker()() as s:
            print(f"暂存表状态：{await staging.status_counts(s)}")
        return 0
    finally:
        try:
            await close_milvus()
        finally:
            await dispose_engine()


def main() -> int:
    parser = argparse.ArgumentParser(description="从历史对话挖掘问答对")
    parser.add_argument("--date", type=date.fromisoformat, default=date.today() - timedelta(days=1))
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)
    try:
        return asyncio.run(run(args.date))
    except Exception:
        logger.exception("挖掘任务失败")
        print("挖掘任务失败")
        return 1


if __name__ == "__main__":
    sys.exit(main())
