"""用人工标注样例检查忠实度裁判。"""

import argparse
import asyncio
import json
import logging
from pathlib import Path
import sys

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent))

from app.llm import get_faith_judge
from app.schemas import FaithVerdict
from app.services.grounding import Citation, format_evidence

logger = logging.getLogger(__name__)
SAMPLES_PATH = SCRIPT_DIR / "faith_judge_samples.jsonl"


async def evaluate_sample(sample: dict, semaphore: asyncio.Semaphore) -> tuple[bool, bool, str]:
    async with semaphore:
        try:
            citations = [Citation(e["n"], 0, e["section_path"], e["question"], e["answer"])
                         for e in sample["evidence"]]
            result = await get_faith_judge().ainvoke({
                "evidence": format_evidence(citations) or "（无证据）", "answer": sample["answer"],
            })
            if result["parsed"] is None:
                raise ValueError("裁判结果无效")
            verdict = FaithVerdict.model_validate(result["parsed"])
        except Exception:
            logger.exception("忠实度裁判评估失败：%s", sample["id"])
            return False, True, f"❌ {sample['id']} 期望 {sample['faithful']} 实际 调用失败 忠实度裁判评估失败"
    passed = verdict.faithful == sample["faithful"]
    marker = "✅" if passed else "❌"
    return passed, False, (f"{marker} {sample['id']} 期望 {sample['faithful']} "
                           f"实际 {verdict.faithful} {verdict.reason}")


async def run_eval() -> int:
    samples = [json.loads(line) for line in SAMPLES_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not samples:
        raise ValueError("样例集为空")
    semaphore = asyncio.Semaphore(4)
    results = await asyncio.gather(*(evaluate_sample(sample, semaphore) for sample in samples))
    for _, _, line in results:
        print(line)
    correct = sum(passed for passed, _, _ in results)
    rate = correct / len(results)
    print(f"忠实度裁判准确率：{rate:.2%} ({correct}/{len(results)})")
    return 0 if rate >= 0.90 and not any(failed for _, failed, _ in results) else 1


def main() -> int:
    argparse.ArgumentParser(description="评估忠实度裁判，正常运行会调用上游模型。").parse_args()
    logging.basicConfig(level=logging.ERROR)
    try:
        return asyncio.run(run_eval())
    except Exception:
        logger.exception("忠实度裁判评估执行失败")
        print("忠实度裁判评估执行失败")
        return 1


if __name__ == "__main__":
    sys.exit(main())
