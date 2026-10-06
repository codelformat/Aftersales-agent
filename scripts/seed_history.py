"""把样例历史对话写入指定日期。"""

import argparse
import asyncio
from datetime import date, timedelta
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.engine import dispose_engine
from app.knowledge.history_seed import HISTORY_FILE, seed_history

logger = logging.getLogger(__name__)


async def run(args) -> int:
    try:
        count = await seed_history(args.date, args.file)
        print(f"写入 {count} 通历史对话" if count else "该日期已有种子会话，跳过")
        return 0
    finally:
        await dispose_engine()


def main() -> int:
    parser = argparse.ArgumentParser(description="导入样例历史对话")
    parser.add_argument("--date", type=date.fromisoformat, default=date.today() - timedelta(days=1),
                        help="会话日期，格式 YYYY-MM-DD，默认昨天")
    parser.add_argument("--file", type=Path, default=HISTORY_FILE, help="历史对话 JSONL 文件")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)
    try:
        return asyncio.run(run(args))
    except Exception:
        logger.exception("历史对话导入失败")
        print("历史对话导入失败")
        return 1


if __name__ == "__main__":
    sys.exit(main())
