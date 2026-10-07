"""用标注样例评估首次调用的工具选择，不执行工具。"""

import argparse
import asyncio
from datetime import date
import json
import logging
from pathlib import Path
import sys

# 用脚本位置定位项目和样例文件。
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent))

from app.llm import get_chat_model
from app.prompts import chat_prompt, chat_prompt_vars
from app.tools.registry import get_registry

logger = logging.getLogger(__name__)
SAMPLES_PATH = SCRIPT_DIR / "tool_selection_samples.jsonl"


async def evaluate_sample(
    index: int, sample: dict, semaphore: asyncio.Semaphore
) -> tuple[bool, bool, str]:
    expected_tools = set(sample["expected_tools"])
    expected_terms: list[str] | None = sample["faq_terms"]
    async with semaphore:
        try:
            response = await (
                chat_prompt
                | get_chat_model().bind_tools(
                    get_registry().tools_for_model(), tool_choice="auto"
                )
            ).ainvoke({
                **chat_prompt_vars(date.today()),
                "history": [],
                "input": sample["text"],
            })
            actual_tools = {call["name"] for call in response.tool_calls}
            questions = [
                call["args"].get("question")
                for call in response.tool_calls
                if call["name"] == "query_faq"
            ]
        except Exception:
            logger.exception("样例 %d 工具选择评估失败", index)
            line = f"{index:02d} ❌ {sorted(expected_tools)!r} → 评估失败"
            return False, False, line

    tools_correct = actual_tools == expected_tools
    question_correct = expected_terms is None or any(
        isinstance(question, str) and all(term in question for term in expected_terms)
        for question in questions
    )
    marker = "✅" if tools_correct and question_correct else "❌"
    line = (
        f"{index:02d} {marker} "
        f"{sorted(expected_tools)!r} → {sorted(actual_tools)!r}"
    )
    if questions or expected_terms is not None:
        line += f" | query_faq question: {questions!r}"
    return tools_correct, question_correct, line


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
    tools_correct = sum(result[0] for result in results)
    faq_results = [
        result for sample, result in zip(samples, results)
        if sample["faq_terms"] is not None
    ]
    faq_total = len(faq_results)
    faq_correct = sum(result[1] for result in faq_results)
    tools_rate = tools_correct / total
    faq_rate = faq_correct / faq_total if faq_total else 1.0
    print(f"工具集合完全匹配率: {tools_rate:.2%} ({tools_correct}/{total})")
    print(f"FAQ 原话包含率: {faq_rate:.2%} ({faq_correct}/{faq_total})")
    passed = tools_rate >= 0.9 and faq_rate == 1.0
    return 0 if passed else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="评估工具选择样例，正常运行会调用上游模型。")
    parser.parse_args()
    logging.basicConfig(level=logging.ERROR)
    try:
        return asyncio.run(run_eval())
    except Exception:
        logger.exception("工具选择评估执行失败")
        print("工具选择评估执行失败")
        return 1


if __name__ == "__main__":
    sys.exit(main())
