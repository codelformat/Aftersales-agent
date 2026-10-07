"""逐条运行客服样例，打印回复和自动检查结果。"""

import argparse
import asyncio
from collections.abc import Iterator
import json
import logging
from pathlib import Path
import sys

import httpx

# 用脚本位置定位项目和样例文件。
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent))

from app.db.engine import dispose_engine
from app.graph.builder import open_graph
from app.knowledge.milvus import close_milvus
from app.main import app

logger = logging.getLogger(__name__)
SAMPLES_PATH = SCRIPT_DIR / "chat_samples.md"


def load_samples() -> list[tuple[str, str]]:
    samples = []
    title = None
    for line in SAMPLES_PATH.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            title = line.removeprefix("## ").strip()
        elif line.startswith("- 用户："):
            message = line.removeprefix("- 用户：").strip()
            if not title or not message:
                raise ValueError("样例缺少标题或用户消息")
            samples.append((title, message))
    if not samples:
        raise ValueError("样例集为空")
    return samples


def parse_sse(text: str) -> Iterator[tuple[str, dict]]:
    event = "message"
    data = []
    for line in [*text.splitlines(), ""]:
        if not line:
            if data:
                yield event, json.loads("\n".join(data))
            event = "message"
            data = []
            continue
        if line.startswith(":"):
            continue
        field, separator, value = line.partition(":")
        if separator and value.startswith(" "):
            value = value[1:]
        if field == "event":
            event = value
        elif field == "data":
            data.append(value)


async def run_samples() -> int:
    try:
        samples = load_samples()
        async with open_graph():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://eval", timeout=None
            ) as client:
                for title, message in samples:
                    response = await client.post(
                        "/chat/stream", json={"user_id": "eval-chat-samples", "message": message}
                    )
                    response.raise_for_status()
                    tools = []
                    actions = []
                    tool_results = {}
                    tokens = []
                    last_event = "无事件"
                    for event, data in parse_sse(response.text):
                        last_event = event
                        if event == "tool_start":
                            tools.extend(data["tools"])
                        elif event == "tool_end":
                            for tool in data["tools"]:
                                tool_results[tool["id"]] = tool["ok"]
                        elif event == "actions":
                            actions = [o["type"] for o in data["options"]]
                        elif event == "token":
                            tokens.append(data["text"])

                    reply = "".join(tokens)
                    length = len(reply)
                    length_passed = length <= 200
                    format_passed = "**" not in reply and not any(
                        line.startswith("#") for line in reply.splitlines()
                    )
                    markers_passed = not any(
                        marker in reply for marker in ("DSML", "<｜", "invoke name")
                    )
                    print(f"\n{title}")
                    print(f"用户：{message}")
                    print("调用的工具：")
                    if tools:
                        for tool in tools:
                            args = json.dumps(tool["args"], ensure_ascii=False)
                            ok = tool_results.get(tool["id"], "未返回")
                            print(f"  {tool['name']} 参数：{args}；ok：{ok}")
                    else:
                        print("  无")
                    print(f"人工选项：{actions or '无'}")
                    print(f"回复：\n{reply}")
                    print(f"回复字数：{length}")
                    print(f"字数 ≤ 200：{'通过' if length_passed else '不通过'}")
                    print(f"不含 ** 且没有以 # 开头的行：{'通过' if format_passed else '不通过'}")
                    print(f"不含工具调用标记：{'通过' if markers_passed else '不通过'}")
                    if last_event != "done":
                        print(f"失败：{last_event}")
                        return 1
        # 自动检查只供人工参考，不影响退出码。
        return 0
    finally:
        try:
            await close_milvus()
        finally:
            await dispose_engine()


def main() -> int:
    parser = argparse.ArgumentParser(description="检查客服样例，正常运行会调用上游模型并写入真实数据库。")
    parser.parse_args()
    logging.basicConfig(level=logging.ERROR)
    try:
        return asyncio.run(run_samples())
    except Exception:
        logger.exception("客服样例检查执行失败")
        print("客服样例检查执行失败")
        return 1


if __name__ == "__main__":
    sys.exit(main())
