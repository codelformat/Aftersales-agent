"""证据合并、渲染、自评与入池。线上聊天和评估共用。"""

import asyncio
import json
import logging
import re
from dataclasses import asdict, dataclass

from langchain_core.runnables import Runnable

import app.config as config
from app.db.engine import get_sessionmaker
from app.llm import get_self_checker
from app.repositories import low_confidence
from app.schemas import SelfCheck

logger = logging.getLogger(__name__)

EMPTY_EVIDENCE_REASON = "检索证据低于置信度门槛"
SELF_CHECK_FAILED_REASON = "自评调用失败，按通过处理"
REFUSED_CONTENT = json.dumps(
    {"ok": True, "data": {"evidence": [], "answerable": False}}, ensure_ascii=False
)
_CITATION_RE = re.compile(r"\[(\d+)\]")


@dataclass(frozen=True)
class Citation:
    n: int
    chunk_id: int
    section_path: str
    question: str
    answer: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Evidence:
    questions: list[str]
    citations: list[Citation]
    by_call: dict[str, list[Citation]]


def collect_evidence(calls: list[tuple[str, str, dict]]) -> Evidence:
    """按调用顺序拼接证据。chunk_id 重复时只保留第一次出现的那条。编号全局连续。"""
    seen: set[int] = set()
    citations: list[Citation] = []
    by_call: dict[str, list[Citation]] = {}
    for call_id, _, data in calls:
        mine = []
        for e in data.get("evidence", []):
            if e["chunk_id"] in seen:
                continue
            seen.add(e["chunk_id"])
            c = Citation(len(citations) + 1, e["chunk_id"], e["section_path"], e["question"], e["answer"])
            citations.append(c)
            mine.append(c)
        by_call[call_id] = mine
    return Evidence([q for _, q, _ in calls], citations, by_call)


def render_evidence(citations: list[Citation]) -> str:
    """模型看到的工具消息内容。不含 chunk_id 和分数。"""
    return json.dumps({"ok": True, "data": {"evidence": [
        {"n": c.n, "section_path": c.section_path, "content": f"问：{c.question}\n答：{c.answer}"}
        for c in citations
    ]}}, ensure_ascii=False)


def format_evidence(citations: list[Citation]) -> str:
    return "\n\n".join(f"[{c.n}] {c.section_path}\n问：{c.question}\n答：{c.answer}" for c in citations)


def parse_citations(text: str) -> list[int]:
    out: list[int] = []
    for m in _CITATION_RE.finditer(text):
        n = int(m.group(1))
        if n not in out:
            out.append(n)
    return out


async def self_check(
    questions: list[str], citations: list[Citation], *, checker: Runnable | None = None
) -> SelfCheck:
    if not citations:
        return SelfCheck(useful=False, reason=EMPTY_EVIDENCE_REASON)
    # 工厂在 try 之外调用：配置错误和测试中未替换时立即暴露。
    checker = checker or get_self_checker()
    try:
        result = await asyncio.wait_for(
            checker.ainvoke(
                {"question": "\n".join(questions), "evidence": format_evidence(citations)}
            ),
            config.SELF_CHECK_TIMEOUT_SECONDS,
        )
        if result["parsed"] is None:
            raise ValueError(f"自评结果无效：raw={result.get('raw')!r}")
        return result["parsed"]
    except Exception:
        # 证据已通过重排门槛，按通过处理。
        logger.exception("自评调用失败，按通过处理")
        return SelfCheck(useful=True, reason=SELF_CHECK_FAILED_REASON)


async def record_low_confidence(
    conversation_id: int, raw_question: str, reason: str, source: str = "self_check"
) -> None:
    """独立事务。失败只记日志，不中断本轮。"""
    try:
        async with get_sessionmaker()() as s:
            await low_confidence.add(
                s, conversation_id=conversation_id, raw_question=raw_question,
                source=source, reason=reason,
            )
            await s.commit()
    except Exception:
        logger.exception("低置信度问题入池失败")
