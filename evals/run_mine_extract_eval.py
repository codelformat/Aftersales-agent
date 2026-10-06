"""用标注样例评估历史对话中的问答抽取。"""

import argparse
import asyncio
import json
import logging
from pathlib import Path
import sys

# 用脚本位置定位项目和样例文件。
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent))

from app.db.models import Message
from app.knowledge import mining
from app.llm import get_qa_extractor
from app.schemas import QaPairs

logger = logging.getLogger(__name__)
SAMPLES_PATH = SCRIPT_DIR / "mine_extract_samples.jsonl"


async def evaluate_sample(
    index: int, sample: dict, semaphore: asyncio.Semaphore
) -> tuple[bool, str]:
    async with semaphore:
        try:
            rows = [
                Message(role=message["role"], content=message["content"])
                for message in sample["messages"]
            ]
            text = mining.format_transcript(rows)
            result = await get_qa_extractor().ainvoke({"transcript": text})
            parsed = result["parsed"]
            if parsed is None:
                error = result.get("parsing_error")
                if isinstance(error, Exception):
                    raise error
                raise ValueError("抽取结果无法解析")
            actual = QaPairs.model_validate(parsed)
        except Exception:
            logger.exception("样例 %d 问答抽取评估失败", index)
            return False, f"{index:02d} ❌ 问答抽取评估失败"

    count_correct = sample["min_pairs"] <= len(actual.pairs) <= sample["max_pairs"]
    forbidden_correct = not any(
        forbidden in text
        for pair in actual.pairs
        for text in (pair.question, pair.answer)
        for forbidden in sample["forbidden"]
    )
    passed = count_correct and forbidden_correct
    marker = "✅" if passed else "❌"
    line = (
        f"{index:02d} {marker} 问答对数量: {len(actual.pairs)} "
        f"| 期望: [{sample['min_pairs']}, {sample['max_pairs']}] "
        f"| 禁用子串检查: {'通过' if forbidden_correct else '失败'}"
    )
    for rank, pair in enumerate(actual.pairs, start=1):
        line += f"\n  {rank}. 问：{pair.question!r}\n     答：{pair.answer!r}"
    if not actual.pairs:
        line += "\n  无问答对"
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
    print(f"问答抽取通过率: {rate:.2%} ({correct}/{total})")
    return 0 if rate >= 0.9 else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="评估问答抽取样例，正常运行会调用上游模型。"
    )
    parser.parse_args()
    logging.basicConfig(level=logging.ERROR)
    try:
        return asyncio.run(run_eval())
    except Exception:
        logger.exception("问答抽取评估执行失败")
        print("问答抽取评估执行失败")
        return 1


if __name__ == "__main__":
    sys.exit(main())
