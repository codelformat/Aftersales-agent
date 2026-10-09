"""用标注样例评估待审问题查重，正常运行会调用真实上游。"""

import argparse
import asyncio
import json
import logging
from pathlib import Path
import sys

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent))

from app.flywheel.pipeline import format_candidates
from app.llm import get_review_dedup_judge
from app.schemas import ReviewDedup

logger = logging.getLogger(__name__)
SAMPLES_PATH = SCRIPT_DIR / "review_dedup_samples.jsonl"


async def evaluate_sample(
    index: int, sample: dict, semaphore: asyncio.Semaphore
) -> tuple[bool, bool, str]:
    async with semaphore:
        try:
            result = await get_review_dedup_judge().ainvoke({
                "question": sample["question"],
                "candidates": format_candidates([(0, c) for c in sample["candidates"]]),
            })
            parsed = result["parsed"]
            if parsed is None:
                raise ValueError(f"结果无法解析：{result.get('parsing_error')}")
            actual = ReviewDedup.model_validate(parsed)
        except Exception as exc:
            logger.exception("样例 %d 待审查重评估失败", index)
            return False, False, f"{index:02d} ❌ {sample['question']!r} | 调用或解析失败：{exc}"

    duplicate_of = actual.duplicate_of
    index_correct = duplicate_of is None or 1 <= duplicate_of <= len(sample["candidates"])
    passed = index_correct and duplicate_of == sample["expected"]
    reason = "判定与标注一致" if passed else "判定与标注不一致"
    if not index_correct:
        reason += "；候选序号越界"
    line = (
        f"{index:02d} {'✅' if passed else '❌'} {sample['question']!r}"
        f" | {reason} | 期望：{sample['expected']!r} → 实际：{duplicate_of!r}"
        f" | 分类：{sample['kind']}"
    )
    return passed, True, line


async def run_eval() -> int:
    samples = [
        json.loads(line)
        for line in SAMPLES_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not samples:
        raise ValueError("样例集为空")
    semaphore = asyncio.Semaphore(3)
    results = await asyncio.gather(
        *(evaluate_sample(index, sample, semaphore)
          for index, sample in enumerate(samples, start=1))
    )
    for _, _, line in results:
        print(line)
    total = len(results)
    parsed = sum(result[1] for result in results)
    passed = sum(result[0] for result in results)
    print(f"JSON 解析率：{parsed / total:.2%} ({parsed}/{total})")
    print(f"待审查重准确率：{passed / total:.2%} ({passed}/{total})")
    return 0 if passed == total else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    logging.basicConfig(level=logging.ERROR)
    try:
        return asyncio.run(run_eval())
    except Exception:
        logger.exception("待审查重评估执行失败")
        print("待审查重评估执行失败")
        return 1


if __name__ == "__main__":
    sys.exit(main())
