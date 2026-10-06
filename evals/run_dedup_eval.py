"""用标注样例评估问答去重裁定。"""

import argparse
import asyncio
import json
import logging
from pathlib import Path
import sys

# 用脚本位置定位项目和样例文件。
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent))

from app.knowledge import mining
from app.llm import get_dedup_judge
from app.schemas import DedupVerdict

logger = logging.getLogger(__name__)
SAMPLES_PATH = SCRIPT_DIR / "dedup_samples.jsonl"


async def evaluate_sample(
    index: int, sample: dict, semaphore: asyncio.Semaphore
) -> tuple[bool, str]:
    async with semaphore:
        try:
            candidates = [(question, answer) for question, answer in sample["candidates"]]
            result = await get_dedup_judge().ainvoke({
                "question": sample["question"],
                "answer": sample["answer"],
                "candidates": mining.format_candidates(candidates),
            })
            parsed = result["parsed"]
            if parsed is None:
                error = result.get("parsing_error")
                if isinstance(error, Exception):
                    raise error
                raise ValueError("裁定结果无法解析")
            actual = DedupVerdict.model_validate(parsed)
        except Exception:
            logger.exception("样例 %d 去重裁定评估失败", index)
            return False, f"{index:02d} ❌ 去重裁定评估失败"

    duplicate = actual.duplicate_of is not None
    index_correct = not duplicate or 1 <= actual.duplicate_of <= len(candidates)
    passed = duplicate == sample["expected_duplicate"] and index_correct
    marker = "✅" if passed else "❌"
    line = (
        f"{index:02d} {marker} {sample['question']!r} "
        f"| 期望重复: {sample['expected_duplicate']} → {duplicate} "
        f"| duplicate_of: {actual.duplicate_of!r} | 分类: {actual.category}"
    )
    if not index_correct:
        line += " | 候选序号越界"
    return passed, line


async def run_eval() -> int:
    samples = [
        json.loads(line)
        for line in SAMPLES_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not samples:
        raise ValueError("样例集为空")

    semaphore = asyncio.Semaphore(4)
    results = await asyncio.gather(
        *(evaluate_sample(index, sample, semaphore)
          for index, sample in enumerate(samples, start=1))
    )
    # 按样例顺序打印，避免并发完成顺序影响人工检查。
    for _, line in results:
        print(line)

    total = len(results)
    correct = sum(result[0] for result in results)
    rate = correct / total
    print(f"去重裁定准确率: {rate:.2%} ({correct}/{total})")
    return 0 if rate >= 0.9 else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="评估去重裁定样例，正常运行会调用上游模型。"
    )
    parser.parse_args()
    logging.basicConfig(level=logging.ERROR)
    try:
        return asyncio.run(run_eval())
    except Exception:
        logger.exception("去重裁定评估执行失败")
        print("去重裁定评估执行失败")
        return 1


if __name__ == "__main__":
    sys.exit(main())
