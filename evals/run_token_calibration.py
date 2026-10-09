"""字符/token 口径校准：对比 count_tokens 估算和上游 usage。只打印建议值，不改代码。"""

import asyncio
import json
import logging
import math
import statistics
import sys
from datetime import date
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(ROOT))

from langchain_core.messages import HumanMessage

from app.config import CHARS_PER_TOKEN, get_settings
from app.context import count_tokens
from app.llm import build_extract_model
from app.prompts import render_agent_system
from app.tools import mock_data

logger = logging.getLogger(__name__)


def samples() -> list[tuple[str, str]]:
    today = date(2026, 10, 8)
    out = [("system", render_agent_system())]
    for path in sorted((ROOT / "knowledge" / "docs").glob("*/*.md"))[:5]:
        out.append(("evidence", path.read_text(encoding="utf-8")[:400]))
    for oid in ("1001", "1002"):
        out.append(("tool", json.dumps({"ok": True, "data": mock_data.order(oid, today)}, ensure_ascii=False)))
    out.append(("tool", json.dumps({"ok": True, "data": mock_data.logistics("1001", today)}, ensure_ascii=False)))
    dialog = json.loads((ROOT / "scripts" / "demo7_dialog.json").read_text(encoding="utf-8"))
    out += [("user", line) for line in dialog[:10]]
    return out


async def input_tokens(model, text: str) -> int:
    msg = await model.ainvoke([HumanMessage(text)], max_tokens=16)
    return msg.usage_metadata["input_tokens"]


async def run() -> int:
    model = build_extract_model(get_settings())
    # 基线：一条最短消息，扣掉上游模板的固定开销。
    base = await input_tokens(model, "好")
    rows = []
    for kind, text in samples():
        real = await input_tokens(model, text) - base + 1
        estimate = count_tokens([HumanMessage(text)]) - count_tokens([HumanMessage("好")]) + 1
        rows.append((kind, len(text), estimate, real, len(text) / real))
        print(f"{kind:8s} 字数={len(text):5d} 估算={estimate:5d} 真实={real:5d} 字/token={len(text) / real:.2f}")
    ratios = [r[4] for r in rows]
    suggest = math.floor(min(ratios) * 10) / 10
    print(f"\n当前 CHARS_PER_TOKEN={CHARS_PER_TOKEN}；最小 {min(ratios):.2f}，中位数 {statistics.median(ratios):.2f}")
    print(f"建议 CHARS_PER_TOKEN={suggest}（取最小值向下到 0.1，估算不低于真实值）")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.ERROR)
    sys.exit(asyncio.run(run()))
