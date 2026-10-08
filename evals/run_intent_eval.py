# evals/run_intent_eval.py
"""评估意图识别：准确率 ≥ 90%、JSON 解析率和「其他」召回均为 100% 才通过。"""

import asyncio
from collections import Counter
import json
import logging
from pathlib import Path
import sys

# 用脚本位置定位项目和样例文件。
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent))

from app.llm import get_intent_classifier
from app.schemas import INTENTS

logger = logging.getLogger(__name__)
SAMPLES_PATH = SCRIPT_DIR / "intent_samples.jsonl"
PASS_RATE = 0.90
CONFIDENCE_BANDS = (("<0.5", 0.5), ("0.5–0.7", 0.7), ("0.7–0.9", 0.9), ("≥0.9", float("inf")))


async def classify(sample: dict, semaphore: asyncio.Semaphore) -> tuple[str, float | None]:
    async with semaphore:
        try:
            result = await get_intent_classifier().ainvoke({"text": sample["text"]})
            if result["parsed"] is None:
                raise ValueError(f"解析失败：{result.get('raw')!r}")
            return result["parsed"].intent, result["parsed"].confidence
        except Exception:
            logger.exception("意图识别失败：%s", sample["text"])
            return "失败", None


async def run_eval() -> int:
    samples = [json.loads(line) for line in SAMPLES_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not samples:
        raise ValueError("样例集为空")
    semaphore = asyncio.Semaphore(4)
    actual = await asyncio.gather(*(classify(s, semaphore) for s in samples))
    confusion = Counter()
    band_counts = Counter()
    band_correct = Counter()
    correct = 0
    parsed = 0
    other_total = 0
    other_correct = 0
    for i, (sample, (got, confidence)) in enumerate(zip(samples, actual), 1):
        ok = got == sample["intent"]
        correct += ok
        parsed += got != "失败"
        if sample["intent"] == "其他":
            other_total += 1
            other_correct += got == "其他"
        if confidence is not None:
            for label, upper in CONFIDENCE_BANDS:
                if confidence < upper:
                    band_counts[label] += 1
                    band_correct[label] += ok
                    break
        confusion[(sample["intent"], got)] += 1
        print(f"{i:02d} {'✅' if ok else '❌'} {sample['text']} | 期望 {sample['intent']} → {got}"
              f" | confidence={confidence}")
    labels = [*INTENTS, "失败"]
    print("\n混淆矩阵（行：期望，列：实际）")
    print("\t" + "\t".join(labels))
    for expected in labels:
        print(expected + "\t" + "\t".join(str(confusion[(expected, got)]) for got in labels))
    rate = correct / len(samples)
    print(f"\n准确率：{correct}/{len(samples)} = {rate:.1%}（门槛 {PASS_RATE:.0%}）")
    print(f"JSON 解析率：{parsed}/{len(samples)} = {parsed / len(samples):.1%}（门槛 100%）")
    if other_total:
        print(f"「其他」召回：{other_correct}/{other_total} = {other_correct / other_total:.1%}（门槛 100%）")
    else:
        print("「其他」召回：N/A（没有「其他」样例，不能达标）")
    print("\n置信度分档（下界包含、上界不包含；失败不计入分档）")
    for label, _ in CONFIDENCE_BANDS:
        count = band_counts[label]
        accuracy = f"{band_correct[label] / count:.1%}" if count else "N/A"
        print(f"{label}：条数 {count}，准确率 {accuracy}")
    passed = rate >= PASS_RATE and parsed == len(samples) and other_total > 0 and other_correct == other_total
    return 0 if passed else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.ERROR)
    sys.exit(asyncio.run(run_eval()))
