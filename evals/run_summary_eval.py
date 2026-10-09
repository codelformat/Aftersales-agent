"""评估摘要 Prompt：必留事实、无编造数字、不复述、长度全部通过才达标。"""

import asyncio
import json
import logging
from pathlib import Path
import sys

# 用脚本位置定位项目和样例文件。
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent))

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from app.config import SUMMARY_TIMEOUT_SECONDS
from app.context.summarizer import SummaryRejected, batch_text, check_summary
from app.llm import get_summarizer

logger = logging.getLogger(__name__)
SAMPLES_PATH = SCRIPT_DIR / "summary_samples.jsonl"
MIN_CHARS, MAX_CHARS = 30, 200


def to_messages(batch: list[dict]) -> list[BaseMessage]:
    out = []
    for message in batch:
        if message["role"] == "user":
            out.append(HumanMessage(message["content"]))
        elif message["role"] == "tool":
            out.append(ToolMessage(message["content"], tool_call_id=message["tool_call_id"]))
        else:
            out.append(AIMessage(message.get("content") or "", tool_calls=[
                {"id": call["id"], "name": call["name"], "args": call["args"]}
                for call in message.get("tool_calls", [])
            ]))
    return out


async def summarize(sample: dict, semaphore: asyncio.Semaphore) -> tuple[str | None, str, str]:
    dialog = batch_text(to_messages(sample["batch"]))
    previous = "\n".join(f"第{i}段：{content}" for i, content in enumerate(sample["previous"], 1)) or "（无）"
    async with semaphore:
        try:
            text = await asyncio.wait_for(
                get_summarizer().ainvoke({"previous": previous, "dialog": dialog}), SUMMARY_TIMEOUT_SECONDS)
            return text.strip(), dialog, previous
        except Exception:
            logger.exception("摘要失败：%s", sample["id"])
            return None, dialog, previous


def problems(sample: dict, text: str | None, dialog: str, previous: str) -> list[str]:
    if text is None:
        return ["调用失败"]
    found = []
    try:
        check_summary(text, dialog + "\n" + previous)
    except SummaryRejected as exc:
        found.append(f"校验失败：{exc.code}")
    found += [f"缺少：{word}" for word in sample["must_contain"] if word not in text]
    found += [f"不应出现：{word}" for word in sample["must_not_contain"] if word in text]
    if sample.get("chitchat") and text != "本批无售后相关内容。":
        found.append("闲聊样例必须只输出：本批无售后相关内容。")
    if len(text) > MAX_CHARS or (not sample.get("chitchat") and len(text) < MIN_CHARS):
        found.append(f"长度 {len(text)} 不在 {MIN_CHARS}–{MAX_CHARS}")
    return found


async def run_eval() -> int:
    samples = [json.loads(line) for line in SAMPLES_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not samples:
        raise ValueError("样例集为空")
    semaphore = asyncio.Semaphore(3)
    results = await asyncio.gather(*(summarize(sample, semaphore) for sample in samples))
    passed = 0
    for sample, (text, dialog, previous) in zip(samples, results):
        found = problems(sample, text, dialog, previous)
        passed += not found
        print(f"{sample['id']} {'✅' if not found else '❌'} {text}")
        for problem in found:
            print(f"  {problem}")
    print(f"\n全部检查通过：{passed}/{len(samples)}（门槛 100%）")
    return 0 if passed == len(samples) else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.ERROR)
    sys.exit(asyncio.run(run_eval()))
