"""用标注样例评估售后提取结果。"""

import argparse
import asyncio
import json
import logging
from pathlib import Path
import sys

# 用脚本位置定位项目和样例文件。
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent))

from app.llm import get_extractor
from app.schemas import AfterSalesRequest

logger = logging.getLogger(__name__)
SAMPLES_PATH = SCRIPT_DIR / "extract_samples.jsonl"


async def evaluate_sample(
    index: int, sample: dict, semaphore: asyncio.Semaphore
) -> tuple[bool, bool, str]:
    expected = sample["expected"]
    async with semaphore:
        try:
            result = await get_extractor().ainvoke({"text": sample["text"]})
            parsed = result["parsed"]
            if parsed is None:
                error = result.get("parsing_error")
                if isinstance(error, Exception):
                    raise error
                raise ValueError("提取结果无法解析")
            actual = AfterSalesRequest.model_validate(parsed)
        except Exception as exc:
            logger.exception("样例 %d 提取失败", index)
            line = (
                f"{index:02d} ❌ "
                f"order_id: {expected['order_id']!r} → 提取失败 | "
                f"request_type: {expected['request_type']!r} → 提取失败 | "
                f"expected_solution: 提取失败 | 异常类型: {type(exc).__name__}"
            )
            # 失败时两个字段都计错，包括期望订单号为 null 的样例。
            return False, False, line

    order_correct = actual.order_id == expected["order_id"]
    type_correct = actual.request_type.value == expected["request_type"]
    marker = "✅" if order_correct and type_correct else "❌"
    line = (
        f"{index:02d} {marker} "
        f"order_id: {expected['order_id']!r} → {actual.order_id!r} | "
        f"request_type: {expected['request_type']!r} → {actual.request_type.value!r} | "
        f"expected_solution: {actual.expected_solution!r}"
    )
    return order_correct, type_correct, line


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
    for _, _, line in results:
        print(line)

    total = len(results)
    order_correct = sum(result[0] for result in results)
    type_correct = sum(result[1] for result in results)
    print(f"order_id 准确率: {order_correct / total:.2%} ({order_correct}/{total})")
    print(f"request_type 准确率: {type_correct / total:.2%} ({type_correct}/{total})")
    passed = type_correct / total >= 0.9 and order_correct == total
    return 0 if passed else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="评估售后提取样例，正常运行会调用上游模型。")
    parser.parse_args()
    logging.basicConfig(level=logging.ERROR)
    try:
        return asyncio.run(run_eval())
    except Exception as exc:
        logger.exception("评估执行失败")
        print(f"评估执行失败 | 异常类型: {type(exc).__name__}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
