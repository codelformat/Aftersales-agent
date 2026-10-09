"""后台摘要：把层 2 的一批消息压成新的一段，只追加，不重写旧段。"""

import asyncio
import logging
import re
import time
from collections.abc import Sequence

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from app.config import (SUMMARY_MAX_CHARS, SUMMARY_RESERVE_TOKENS, SUMMARY_TIMEOUT_SECONDS,
                        SUMMARY_TOOL_RESULT_CHARS)
from app.context import count_tokens
from app.db.engine import get_sessionmaker
from app.llm import get_summarizer
from app.repositories import conversations, summaries

logger = logging.getLogger(__name__)


class SummaryRejected(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def batch_text(batch: Sequence[BaseMessage]) -> str:
    lines = []
    for m in batch:
        if isinstance(m, HumanMessage):
            lines.append(f"用户：{m.content}")
        elif isinstance(m, ToolMessage):
            lines.append(f"工具结果：{str(m.content)[:SUMMARY_TOOL_RESULT_CHARS]}")
        elif isinstance(m, AIMessage):
            for c in m.tool_calls:
                args = ", ".join(f"{k}={v}" for k, v in c["args"].items())
                lines.append(f"调用 {c['name']}({args})")
            if isinstance(m.content, str) and m.content:
                lines.append(f"客服：{m.content}")
    return "\n".join(lines)


def check_summary(text: str, source: str) -> None:
    if not text:
        raise SummaryRejected("empty")
    if len(text) > SUMMARY_MAX_CHARS:
        raise SummaryRejected("too_long")
    # 订单号、手机号等数字串必须来自原文。
    if any(num not in source for num in re.findall(r"\d{4,}", text)):
        raise SummaryRejected("unsupported_number")


def projection(contents: Sequence[str]) -> str:
    """从最新一段往前取，总量不超过梗概预留；至少保留最新一段。"""
    lines = [f"第{i}段：{c}" for i, c in enumerate(contents, 1)]
    kept: list[str] = []
    for line in reversed(lines):
        if kept and count_tokens([HumanMessage("\n".join([line, *kept]))]) > SUMMARY_RESERVE_TOKENS:
            break
        kept.insert(0, line)
    return "\n".join(kept)


async def run_summary(cid: int, batch: list[BaseMessage], from_id: int, upto_id: int) -> None:
    started = time.monotonic()
    logger.info("summary start conversation=%s range=%s..%s msgs=%s", cid, from_id, upto_id, len(batch))
    try:
        sm = get_sessionmaker()
        async with sm() as s:
            previous = [r.content for r in await summaries.list_for_conversation(s, cid)]
        dialog = batch_text(batch)
        prev_text = "\n".join(f"第{i}段：{c}" for i, c in enumerate(previous, 1)) or "（无）"
        text = (await asyncio.wait_for(get_summarizer().ainvoke({"previous": prev_text, "dialog": dialog}),
                                       SUMMARY_TIMEOUT_SECONDS)).strip()
        check_summary(text, dialog + "\n" + prev_text)
        async with sm() as s:
            seq = await summaries.append(s, cid, from_id, upto_id, text)
            if not await conversations.set_summary(s, cid, upto_id, projection([*previous, text])):
                await s.rollback()
                raise SummaryRejected("stale_range")
            await s.commit()
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        code = exc.code if isinstance(exc, SummaryRejected) else type(exc).__name__
        logger.warning("summary fail conversation=%s range=%s..%s elapsed=%.1fs error=%s",
                       cid, from_id, upto_id, time.monotonic() - started, code, exc_info=not isinstance(exc, SummaryRejected))
        return
    logger.info("summary done conversation=%s 第%s段 range=%s..%s chars=%s elapsed=%.1fs",
                cid, seq, from_id, upto_id, len(text), time.monotonic() - started)


class SummaryRunner:
    """每个会话最多一个运行中的摘要任务。持有任务引用，避免被回收。"""

    def __init__(self):
        self._tasks: dict[int, asyncio.Task] = {}

    def running(self, cid: int) -> bool:
        task = self._tasks.get(cid)
        return task is not None and not task.done()

    def start(self, cid: int, batch: Sequence[BaseMessage], from_id: int, upto_id: int,
              tokens: int, budget: int) -> bool:
        if self.running(cid):
            logger.info("summary skip conversation=%s reason=running", cid)
            return False
        logger.info("summary trigger conversation=%s 层2 约 %s token > 预算 %s range=%s..%s",
                    cid, tokens, budget, from_id, upto_id)
        task = asyncio.create_task(run_summary(cid, list(batch), from_id, upto_id))
        self._tasks[cid] = task
        task.add_done_callback(lambda t, c=cid: self._tasks.pop(c, None) if self._tasks.get(c) is t else None)
        return True

    async def drain(self) -> None:
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)

    async def cancel_all(self) -> None:
        for cid, task in list(self._tasks.items()):
            if not task.done():
                task.cancel()
                logger.info("summary cancel conversation=%s", cid)
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)


_runner = SummaryRunner()


def get_runner() -> SummaryRunner:
    return _runner


def set_runner(runner: SummaryRunner) -> None:
    global _runner
    _runner = runner
