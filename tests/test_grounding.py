import asyncio
import json
from time import perf_counter

import pytest
from langchain_core.runnables import RunnableLambda
from sqlalchemy import select

import app.services.grounding as g
import app.config as config

from app.db.models import Conversation, LowConfidenceQuestion
from app.repositories import low_confidence
from app.schemas import SelfCheck

pytestmark = pytest.mark.anyio


def item(cid, path="退货政策 > 运费 > 运费说明", q="运费说明", a="满 99 元免运费", score=0.9):
    return {"chunk_id": cid, "section_path": path, "question": q, "answer": a, "score": score}


def test_collect_evidence_dedupes_and_numbers_across_calls():
    ev = g.collect_evidence([
        ("c1", "运费", {"evidence": [item(1), item(2)]}),
        ("c2", "发票", {"evidence": [item(2), item(3)]}),
    ])
    assert ev.questions == ["运费", "发票"]
    assert [(c.n, c.chunk_id) for c in ev.citations] == [(1, 1), (2, 2), (3, 3)]
    assert [c.n for c in ev.by_call["c1"]] == [1, 2]
    assert [c.n for c in ev.by_call["c2"]] == [3]


def test_render_evidence_hides_ids_and_scores():
    ev = g.collect_evidence([("c1", "运费", {"evidence": [item(7)]})])
    assert json.loads(g.render_evidence(ev.citations)) == {"ok": True, "data": {"evidence": [
        {"n": 1, "section_path": "退货政策 > 运费 > 运费说明", "content": "问：运费说明\n答：满 99 元免运费"},
    ]}}


def test_refused_content():
    assert json.loads(g.REFUSED_CONTENT) == {"ok": True, "data": {"evidence": [], "answerable": False}}


def test_citation_to_dict_matches_faith_cases_format():
    c = g.Citation(1, 7, "p", "q", "a")
    assert c.to_dict() == {"n": 1, "chunk_id": 7, "section_path": "p", "question": "q", "answer": "a"}


def test_format_evidence():
    ev = g.collect_evidence([("c1", "运费", {"evidence": [item(7), item(8, path="p2", q="q2", a="a2")]})])
    assert g.format_evidence(ev.citations) == (
        "[1] 退货政策 > 运费 > 运费说明\n问：运费说明\n答：满 99 元免运费\n\n[2] p2\n问：q2\n答：a2"
    )


def test_parse_citations():
    assert g.parse_citations("可以退[1]。运费 8 元[3][1]。[x] 见[12]") == [1, 3, 12]
    assert g.parse_citations("没有引用") == []


def checker(result=None, exc=None, seen=None):
    def run(inputs):
        if seen is not None:
            seen.append(inputs)
        if exc:
            raise exc
        return {"parsed": result, "raw": "raw"}
    return RunnableLambda(run)


async def test_self_check_empty_evidence_skips_checker():
    res = await g.self_check(["X9 防水吗"], [], checker=checker(exc=AssertionError("不应调用")))
    assert res == SelfCheck(useful=False, reason=g.EMPTY_EVIDENCE_REASON)


async def test_self_check_passes_questions_and_evidence():
    seen = []
    ev = g.collect_evidence([("c1", "运费", {"evidence": [item(7)]})])
    res = await g.self_check(["运费", "发票"], ev.citations,
                             checker=checker(SelfCheck(useful=False, reason="缺发票"), seen=seen))
    assert res.useful is False and res.reason == "缺发票"
    assert seen == [{"question": "运费\n发票", "evidence": g.format_evidence(ev.citations)}]


@pytest.mark.parametrize("bad", [checker(exc=TimeoutError("slow")), checker(result=None)])
async def test_self_check_failure_fails_open(bad, caplog):
    ev = g.collect_evidence([("c1", "运费", {"evidence": [item(7)]})])
    res = await g.self_check(["运费"], ev.citations, checker=bad)
    assert res == SelfCheck(useful=True, reason=g.SELF_CHECK_FAILED_REASON)
    assert "自评调用失败" in caplog.text


async def test_self_check_slow_checker_fails_open_within_budget(monkeypatch, caplog):
    monkeypatch.setattr(config, "SELF_CHECK_TIMEOUT_SECONDS", 0.05, raising=False)

    async def slow_checker(inputs):
        await asyncio.sleep(1)
        return {"parsed": SelfCheck(useful=False, reason="证据不足"), "raw": "raw"}

    ev = g.collect_evidence([("c1", "运费", {"evidence": [item(7)]})])
    started = perf_counter()
    res = await g.self_check(["运费"], ev.citations, checker=RunnableLambda(slow_checker))
    elapsed = perf_counter() - started

    assert elapsed < 0.5
    assert res == SelfCheck(useful=True, reason=g.SELF_CHECK_FAILED_REASON)
    assert "自评调用失败" in caplog.text


async def test_self_check_factory_is_blocked_in_tests():
    ev = g.collect_evidence([("c1", "运费", {"evidence": [item(7)]})])
    with pytest.raises(RuntimeError, match="get_self_checker"):
        await g.self_check(["运费"], ev.citations)


async def test_record_low_confidence_writes_row(db):
    async with db() as s:
        conv = Conversation(user_id="u1")
        s.add(conv)
        await s.commit()
    await g.record_low_confidence(conv.id, "X9 能无线充电吗？急！", "证据没写无线充电")
    async with db() as s:
        rows = list(await s.scalars(select(LowConfidenceQuestion)))
    assert [(r.conversation_id, r.raw_question, r.source, r.reason) for r in rows] == [
        (conv.id, "X9 能无线充电吗？急！", "self_check", "证据没写无线充电"),
    ]


async def test_record_low_confidence_swallows_errors(db, monkeypatch, caplog):
    async def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(low_confidence, "add", boom)
    await g.record_low_confidence(1, "q", "r")
    assert "低置信度问题入池失败" in caplog.text
