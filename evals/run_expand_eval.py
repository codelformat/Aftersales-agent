"""评估查询扩写：解析、条数和型号数字保留检查全部通过才达标。"""

import asyncio
import json
import logging
from pathlib import Path
import sys

# 用脚本位置定位项目和样例文件。
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent))

from app.llm import get_query_expander

logger = logging.getLogger(__name__)
SAMPLES_PATH = SCRIPT_DIR / "expand_samples.jsonl"
PASS_RATE = 1.0


async def expand(sample: dict, semaphore: asyncio.Semaphore) -> list[str] | None:
    async with semaphore:
        try:
            result = await get_query_expander().ainvoke({
                "question": sample["question"],
                "products": "、".join(sample["products"]) or "（无）",
            })
            if result["parsed"] is None:
                raise ValueError(f"解析失败：{result.get('raw')!r}")
            return result["parsed"].queries
        except Exception:
            logger.exception("扩写失败：%s", sample["question"])
            return None


async def run_eval() -> int:
    samples = [json.loads(line) for line in SAMPLES_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not samples:
        raise ValueError("样例集为空")
    semaphore = asyncio.Semaphore(4)
    actual = await asyncio.gather(*(expand(s, semaphore) for s in samples))
    passed_count = 0
    parsed_count = 0
    size_count = 0
    keep_count = 0
    for i, (sample, queries) in enumerate(zip(samples, actual), 1):
        parsed_ok = queries is not None
        size_ok = parsed_ok and 1 <= len(queries) <= 4
        keep_ok = parsed_ok and all(word in query for query in queries for word in sample["keep"])
        ok = parsed_ok and size_ok and keep_ok
        passed_count += ok
        parsed_count += parsed_ok
        size_count += size_ok
        keep_count += keep_ok
        print(f"{i:02d} {'✅' if ok else '❌'} {sample['question']}"
              f" | 解析={parsed_ok} | 条数={size_ok} | 保留={keep_ok}")
        if queries is None:
            print("  扩写失败（详见错误日志）")
        else:
            for j, query in enumerate(queries, 1):
                missing = [word for word in sample["keep"] if word not in query]
                print(f"  {j}. {query}" + (f" | 缺少：{'、'.join(missing)}" if missing else ""))
    total = len(samples)
    rate = passed_count / total
    print(f"\nJSON 解析率：{parsed_count}/{total} = {parsed_count / total:.1%}")
    print(f"条数合格率：{size_count}/{total} = {size_count / total:.1%}")
    print(f"型号数字保留率：{keep_count}/{total} = {keep_count / total:.1%}")
    print(f"全部检查通过率：{passed_count}/{total} = {rate:.1%}（门槛 {PASS_RATE:.0%}）")
    return 0 if rate >= PASS_RATE else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.ERROR)
    sys.exit(asyncio.run(run_eval()))
