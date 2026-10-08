# evals/run_intent_eval.py
"""用标注样例评估意图识别。准确率低于 90% 时退出码为 1。"""

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


async def classify(sample: dict, semaphore: asyncio.Semaphore) -> str:
    async with semaphore:
        try:
            result = await get_intent_classifier().ainvoke({"text": sample["text"]})
            if result["parsed"] is None:
                raise ValueError(f"解析失败：{result.get('raw')!r}")
            return result["parsed"].intent
        except Exception:
            logger.exception("意图识别失败：%s", sample["text"])
            return "失败"


async def run_eval() -> int:
    samples = [json.loads(line) for line in SAMPLES_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not samples:
        raise ValueError("样例集为空")
    semaphore = asyncio.Semaphore(4)
    actual = await asyncio.gather(*(classify(s, semaphore) for s in samples))
    confusion = Counter()
    correct = 0
    for i, (sample, got) in enumerate(zip(samples, actual), 1):
        ok = got == sample["intent"]
        correct += ok
        confusion[(sample["intent"], got)] += 1
        print(f"{i:02d} {'✅' if ok else '❌'} {sample['text']} | 期望 {sample['intent']} → {got}")
    labels = [*INTENTS, "失败"]
    print("\n混淆矩阵（行：期望，列：实际）")
    print("\t" + "\t".join(labels))
    for expected in INTENTS:
        print(expected + "\t" + "\t".join(str(confusion[(expected, got)]) for got in labels))
    rate = correct / len(samples)
    print(f"\n准确率：{correct}/{len(samples)} = {rate:.1%}（门槛 {PASS_RATE:.0%}）")
    return 0 if rate >= PASS_RATE else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.ERROR)
    sys.exit(asyncio.run(run_eval()))
