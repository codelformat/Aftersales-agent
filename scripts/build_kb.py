"""离线建库：文档切分和 faq 表入库（MySQL pending），再向量化写入 Milvus。"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.engine import dispose_engine
from app.knowledge.ingest import ingest_all
from app.knowledge.milvus import close_milvus, ensure_collection, recreate_collection
from app.knowledge.vectorize import SimulatedCrash, format_status, kb_status, vectorize_pending

logger = logging.getLogger(__name__)


async def run(args) -> int:
    try:
        if args.rebuild:
            # 集合结构可能已变化，按 id 删除不够，整体重建。
            await recreate_collection()
        else:
            await ensure_collection()
        if args.status or args.check:
            st = await kb_status()
            print(format_status(st))
            if args.check and (st.pending != 0 or st.milvus != st.total):
                print("检查失败：存在 pending 行，或 Milvus 实体数不等于 MySQL 行数")
                return 1
            return 0
        for title, n in (await ingest_all(rebuild=args.rebuild)).items():
            print(f"入库 {title}：{n} 块" if n else f"跳过 {title}：已入库")
        try:
            n = await vectorize_pending(crash_after_batches=args.crash_after_batches)
        except SimulatedCrash:
            logger.exception("模拟中断")
            print("模拟中断：Milvus 已写入，MySQL 未回填")
            print(format_status(await kb_status()))
            return 1
        print(f"本次向量化 {n} 块")
        print(format_status(await kb_status()))
        return 0
    finally:
        try:
            await close_milvus()
        finally:
            await dispose_engine()


class _BuildParser(argparse.ArgumentParser):
    def parse_args(self, args=None, namespace=None):
        parsed = super().parse_args(args, namespace)
        # 参数冲突须在访问数据库和 Milvus 前退出。
        if parsed.rebuild and (parsed.status or parsed.check):
            self.error("--rebuild 不能与 --status 或 --check 同时使用")
        return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = _BuildParser(description="离线建库")
    parser.add_argument("--rebuild", action="store_true", help="重建 Milvus 集合和全部文档来源；mined 块和 flywheel 块保留并重新向量化")
    parser.add_argument("--crash-after-batches", type=int, default=None, help="故障注入，只用于演示")
    parser.add_argument("--status", action="store_true", help="只打印状态")
    parser.add_argument(
        "--check", action="store_true", help="打印状态；有 pending 行或 Milvus 数量不一致时退出码为 1"
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    logging.basicConfig(level=logging.WARNING)
    try:
        return asyncio.run(run(args))
    except Exception:
        logger.exception("建库失败")
        print("建库失败")
        return 1


if __name__ == "__main__":
    sys.exit(main())
