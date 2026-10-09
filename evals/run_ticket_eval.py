"""评估工单 Prompt 的首次调用，只检查回复和工具参数。"""

import argparse
import asyncio
from datetime import date
import json
import logging
from pathlib import Path
import re
import sys

# 用脚本位置定位项目和样例文件。
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent))

from langchain_core.messages import AIMessage, HumanMessage

from app.context.assemble import build_agent_prompt
from app.graph.nodes.agent import turn_toolset
from app.llm import get_chat_model
from app.prompts import render_reference
from app.tools.toolset import builtin_toolset

logger = logging.getLogger(__name__)
SAMPLES_PATH = SCRIPT_DIR / "ticket_samples.jsonl"


def judge(sample: dict, ai_message: AIMessage) -> tuple[bool, str]:
    calls = [call for call in ai_message.tool_calls if call["name"] == "create_ticket"]
    if sample["expect"] == "ask":
        if calls:
            return False, "问题不具体却调用 create_ticket"
        if not ai_message.text.strip():
            return False, "回复文字为空"
        return True, ""

    if len(calls) != 1:
        return False, f"create_ticket 调用数为 {len(calls)}，应为 1"
    args = calls[0]["args"]
    if args.get("ticket_type") != sample["ticket_type"]:
        return False, "工单类型不符"
    description = args.get("description")
    if not isinstance(description, str):
        return False, "问题描述为空话或过短"
    substantive = re.sub(
        r"(用户)?(要求|希望|申请|需要)?(建立|创建|提交|建)(个|一个|一张)?(售后|投诉|咨询)?工单(处理|跟进)?",
        "", description,
    )
    substantive = re.sub(r"[\W_]+", "", substantive)
    if len(substantive) < 4:
        return False, "问题描述为空话或过短"
    missing = [term for term in sample["must_include"] if term not in description]
    if missing:
        return False, f"问题描述缺少：{'、'.join(missing)}"
    source_texts = [sample["user"], *(text for _, text in sample["history"])]
    source_numbers = {number for text in source_texts for number in re.findall(r"\d{4,}", text)}
    invented = sorted(set(re.findall(r"\d{4,}", description)) - source_numbers)
    if invented:
        return False, f"问题描述编造数字：{'、'.join(invented)}"
    return True, ""


async def evaluate_sample(sample: dict, semaphore: asyncio.Semaphore) -> tuple[bool, str]:
    async with semaphore:
        try:
            state = {
                "ticket_request": True, "route": "business", "resolved_input": sample["user"],
                "messages": [HumanMessage(content=text) if role == "user" else AIMessage(content=text)
                             for role, text in sample["history"]],
            }
            prompt = build_agent_prompt(
                history=state["messages"], summary_upto=None, layer1_from=None, summary=None,
                user_input=state["resolved_input"], reference=render_reference(date.today()), agent_messages=[],
            )
            response = await get_chat_model().bind_tools(
                turn_toolset(state, builtin_toolset()).agent_tools(), tool_choice="auto",
            ).ainvoke(prompt.messages)
            ok, reason = judge(sample, response)
            actual = (json.dumps(response.tool_calls, ensure_ascii=False)
                      if response.tool_calls else response.text[:60])
        except Exception as exc:
            logger.exception("工单评估调用失败：%s", sample["id"])
            return False, f"{sample['id']} ❌ 调用失败：{exc}"
    detail = f" | {reason}" if reason else ""
    return ok, f"{sample['id']} {'✅' if ok else '❌'} {actual}{detail}"


async def run_eval() -> int:
    samples = [json.loads(line) for line in SAMPLES_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(samples) != 12:
        raise ValueError("工单评估集必须包含 12 条样例")
    semaphore = asyncio.Semaphore(3)
    results = await asyncio.gather(*(evaluate_sample(sample, semaphore) for sample in samples))
    for _, line in results:
        print(line)
    correct = sum(ok for ok, _ in results)
    print(f"\n通过 {correct}/12")
    return 0 if correct == 12 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="评估工单首次调用，正常运行会调用上游模型。")
    parser.parse_args()
    logging.basicConfig(level=logging.ERROR)
    try:
        return asyncio.run(run_eval())
    except Exception:
        logger.exception("工单评估执行失败")
        print("工单评估执行失败")
        return 1


if __name__ == "__main__":
    sys.exit(main())
