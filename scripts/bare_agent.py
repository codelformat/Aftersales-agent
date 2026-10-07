"""运行一次裸 Agent 循环，打印每一步。用法：uv run python scripts/bare_agent.py "<问题>"。"""

import asyncio
from datetime import date
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from openai import AsyncOpenAI

from app.agent.bare_loop import run_bare_agent
from app.config import UPSTREAM_TIMEOUT_SECONDS, get_settings


async def main(question: str) -> int:
    s = get_settings()
    extra = {"thinking": {"type": s.chat_thinking}} if s.chat_thinking else None
    async with AsyncOpenAI(
        base_url=s.chat_base_url, api_key=s.chat_api_key.get_secret_value(),
        timeout=UPSTREAM_TIMEOUT_SECONDS, max_retries=0,
    ) as client:
        result = await run_bare_agent(
            client, s.chat_model, question, today=date.today(), extra_body=extra,
        )
    for i, names in enumerate(result.calls, 1):
        print(f"第 {i} 步：调用 {', '.join(names)}")
    print(f"共 {result.steps} 步。答案：\n{result.answer}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print('用法：uv run python scripts/bare_agent.py "<问题>"')
        sys.exit(2)
    sys.exit(asyncio.run(main(sys.argv[1])))
