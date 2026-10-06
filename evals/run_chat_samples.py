"""逐条运行客服样例，打印回复和自动检查结果。"""

import argparse
import asyncio
from datetime import date
import logging
from pathlib import Path
import sys

# 用脚本位置定位项目和样例文件。
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent))

from app.config import SHOP_NAME
from app.llm import get_chat_model
from app.prompts import chat_prompt

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


async def run_samples() -> int:
    samples = load_samples()
    chain = chat_prompt | get_chat_model()
    for title, message in samples:
        response = await chain.ainvoke({
            "shop_name": SHOP_NAME,
            "today": date.today().isoformat(),
            "history": [],
            "input": message,
        })
        reply = response.text
        length = len(reply)
        length_passed = length <= 200
        format_passed = "**" not in reply and not any(
            line.startswith("#") for line in reply.splitlines()
        )
        print(f"\n{title}")
        print(f"用户：{message}")
        print(f"回复：\n{reply}")
        print(f"回复字数：{length}")
        print(f"字数 ≤ 200：{'通过' if length_passed else '不通过'}")
        print(f"不含 ** 且没有以 # 开头的行：{'通过' if format_passed else '不通过'}")
    # 自动检查只供人工参考，不影响退出码。
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="检查客服样例，正常运行会调用上游模型。")
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
