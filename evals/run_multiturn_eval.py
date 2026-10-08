"""评估多轮对话的指代消解和意图识别：全部轮次通过才通过。"""

import asyncio
import json
import logging
from pathlib import Path
import sys

# 用脚本位置定位项目和样例文件。
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent))

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from app.services import understanding

logger = logging.getLogger(__name__)
SAMPLES_PATH = SCRIPT_DIR / "multiturn_samples.jsonl"


async def evaluate_group(
    sample: dict, semaphore: asyncio.Semaphore,
) -> list[tuple[str | None, str | None, list[str]]]:
    results = []
    messages: list[BaseMessage] = []
    async with semaphore:
        for turn in sample["turns"]:
            resolved_input = None
            actual_intent = None
            try:
                history = understanding.history_text(messages)
                resolution = await understanding.resolve(turn["user"], history)
                resolved_input = resolution.resolved_input
                decision = await understanding.classify(resolved_input)
                actual_intent = decision.intent
            except Exception:
                logger.exception("多轮评估调用失败：%s | %s", sample["id"], turn["user"])

            if "unchanged" in turn:
                reference_ok = resolved_input == turn["user"]
            else:
                reference_ok = resolved_input is not None and any(
                    word in resolved_input for word in turn["must_contain_any"]
                )
            errors = []
            if actual_intent != turn["intent"]:
                errors.append("意图错")
            if not reference_ok:
                errors.append("指代错")
            results.append((resolved_input, actual_intent, errors))

            # 固定客服回复写入历史，不使用改写后的问题替代用户原话。
            messages.append(HumanMessage(content=turn["user"]))
            if "assistant" in turn:
                messages.append(AIMessage(content=turn["assistant"]))
    return results


async def run_eval() -> int:
    samples = [json.loads(line) for line in SAMPLES_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not samples or any(not sample["turns"] for sample in samples):
        raise ValueError("样例集为空或包含空对话")
    semaphore = asyncio.Semaphore(3)
    actual = await asyncio.gather(*(evaluate_group(sample, semaphore) for sample in samples))
    correct = 0
    total = 0
    for sample, results in zip(samples, actual):
        for i, (turn, (resolved_input, actual_intent, errors)) in enumerate(zip(sample["turns"], results), 1):
            ok = not errors
            correct += ok
            total += 1
            detail = f" | {'、'.join(errors)}" if errors else ""
            print(f"{sample['id']}-{i} {'✅' if ok else '❌'} {turn['user']} → {resolved_input if resolved_input is not None else '失败'}"
                  f" | {turn['intent']} → {actual_intent or '失败'}{detail}")
    print(f"\n通过轮数：{correct}/{total}（全部通过才达标）")
    return 0 if correct == total else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.ERROR)
    sys.exit(asyncio.run(run_eval()))
