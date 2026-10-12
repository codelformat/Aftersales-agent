"""用人工标注样例检查覆盖率裁判，按组计准确率。"""

import argparse
import asyncio
import json
import logging
from pathlib import Path
import sys

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent))

from app.llm import get_coverage_judge
from evals.coverage import format_references, parse_verdict

logger = logging.getLogger(__name__)
SAMPLES_PATH = SCRIPT_DIR / "coverage_judge_samples.jsonl"
CASE_LABELS = ("完全覆盖", "未提到", "部分组覆盖", "形近型号错配", "要点不全", "等价表述", "额外内容",
               "建议联系客服")


async def evaluate_sample(sample: dict, semaphore: asyncio.Semaphore) -> tuple[int, int, bool, str]:
    expected = sample["covered"]
    keys = [ref["key"] for ref in sample["references"]]
    texts = {ref["key"]: ref["text"] for ref in sample["references"]}
    async with semaphore:
        try:
            raw = await get_coverage_judge().ainvoke({
                "question": sample["question"], "references": format_references(keys, texts),
                "answer": sample["answer"],
            })
            if raw["parsed"] is None:
                raise ValueError("裁判结果无效")
            actual, reasons = parse_verdict(raw["parsed"], len(expected))
        except Exception:
            logger.exception("覆盖率裁判评估失败：%s", sample["id"])
            return 0, len(expected), True, f"❌ {sample['id']} 期望 {expected} 实际 调用失败"
    correct = sum(a == e for a, e in zip(actual, expected))
    marker = "✅" if correct == len(expected) else "❌"
    return correct, len(expected), False, (f"{marker} {sample['id']} 期望 {expected} 实际 {actual} "
                                           f"{'；'.join(reasons)}")


async def run_eval() -> int:
    samples = [json.loads(line) for line in SAMPLES_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not samples:
        raise ValueError("样例集为空")
    semaphore = asyncio.Semaphore(4)
    results = await asyncio.gather(*(evaluate_sample(sample, semaphore) for sample in samples))
    for _, _, _, line in results:
        print(line)
    correct = sum(r[0] for r in results)
    total = sum(r[1] for r in results)
    rate = correct / total
    print(f"覆盖率裁判准确率：{rate:.2%} ({correct}/{total} 组)")
    return 0 if rate >= 0.90 and not any(r[2] for r in results) else 1


def main() -> int:
    argparse.ArgumentParser(description="评估覆盖率裁判，正常运行会调用上游模型。").parse_args()
    logging.basicConfig(level=logging.ERROR)
    try:
        return asyncio.run(run_eval())
    except Exception:
        logger.exception("覆盖率裁判评估执行失败")
        print("覆盖率裁判评估执行失败")
        return 1


if __name__ == "__main__":
    sys.exit(main())
