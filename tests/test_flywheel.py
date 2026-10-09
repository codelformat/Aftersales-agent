import logging

import pytest

from app.flywheel import pipeline
from app.flywheel.runner import FlywheelRunner
from app.repositories import conversations, low_confidence, review_queue
from app.services import grounding
from tests.fakes import FakeEmbeddings

pytestmark = pytest.mark.anyio

SNAP = [{"chunk_id": 1, "section_path": "保温杯 > 清洗", "question": "怎么清洗", "answer": "手洗", "score": 0.31}]


async def _lcq(db, raw, snap=SNAP, source="retrieval_low_conf"):
    async with db() as s:
        c = await conversations.create(s, "u1")
        row = await low_confidence.add(s, conversation_id=c.id, raw_question=raw, source=source, reason="r",
                                       retrieved_chunks=snap)
        await s.commit()
        return row.id


@pytest.mark.parametrize("normalized_question", [
    "保温杯可以用洗碗机清洗吗？", "保温杯可以用洗碗机清洗吗", "保温杯可以用洗碗机清洗吗?",
])
async def test_new_gap_creates_review_row(db, use_flywheel, normalized_question):
    calls = use_flywheel(normalized=[(normalized_question, "（待核实）不建议。")], dedup=[])
    lcq = await _lcq(db, "杯子能扔洗碗机吗？？急")
    res = await pipeline.process(lcq)
    assert res.merged is False and res.candidates == 0
    async with db() as s:
        item = await review_queue.get(s, res.review_id)
        assert (item.normalized_question, item.ai_suggested_answer, item.occurrence_count) == (
            "保温杯可以用洗碗机清洗吗？", "（待核实）不建议。", 1)
        assert (await low_confidence.get(s, lcq)).matched_review_id == res.review_id
    assert "怎么清洗" in calls["normalize"][0]["chunks"] and calls["dedup"] == []


async def test_duplicate_increments(db, use_flywheel, monkeypatch):
    emb = FakeEmbeddings(rules=[("洗碗机", [1.0] + [0.0] * 1023)])
    monkeypatch.setattr(pipeline, "get_embeddings", lambda: emb)
    calls = use_flywheel(normalized=[("保温杯可以用洗碗机清洗吗？", "（待核实）a"),
                                     ("保温杯能放洗碗机洗吗？", "（待核实）b")], dedup=[1])
    first = await pipeline.process(await _lcq(db, "杯子能扔洗碗机吗"))
    second = await pipeline.process(await _lcq(db, "保温杯洗碗机能洗不"))
    assert second.merged is True and second.review_id == first.review_id and second.candidates == 1
    assert "1. 保温杯可以用洗碗机清洗吗？" in calls["dedup"][0]["candidates"]
    async with db() as s:
        assert (await review_queue.get(s, first.review_id)).occurrence_count == 2


async def test_already_matched_is_skipped(db, use_flywheel):
    calls = use_flywheel(normalized=[("Q？", "（待核实）a")], dedup=[])
    lcq = await _lcq(db, "q")
    await pipeline.process(lcq)
    assert await pipeline.process(lcq) is None
    assert len(calls["normalize"]) == 1


async def test_normalize_failure_keeps_null(db, use_flywheel, caplog):
    use_flywheel(normalized=[None], dedup=[])
    lcq = await _lcq(db, "q", snap=None)
    assert await pipeline.process(lcq) is None
    assert "flywheel_failed" in caplog.text and "step=normalize" in caplog.text
    async with db() as s:
        assert (await low_confidence.get(s, lcq)).matched_review_id is None


async def test_dedup_out_of_range_keeps_null(db, use_flywheel, monkeypatch):
    emb = FakeEmbeddings(rules=[("洗碗机", [1.0] + [0.0] * 1023)])
    monkeypatch.setattr(pipeline, "get_embeddings", lambda: emb)
    use_flywheel(normalized=[("保温杯洗碗机A？", "（待核实）"), ("保温杯洗碗机B？", "（待核实）")], dedup=[3])
    await pipeline.process(await _lcq(db, "a"))
    lcq = await _lcq(db, "b")
    assert await pipeline.process(lcq) is None
    async with db() as s:
        assert (await low_confidence.get(s, lcq)).matched_review_id is None


@pytest.mark.parametrize("question, expected", [
    ("能开专票吗", "能开专票吗？"),
    ("能开专票吗?", "能开专票吗？"),
    ("能开专票吗？", "能开专票吗？"),
    ("能开专票吗.", "能开专票吗？"),
    ("能开专票吗。", "能开专票吗？"),
    ("能开专票吗?。 \t\n", "能开专票吗？"),
    ("能开专票吗？ \t\n", "能开专票吗？"),
])
def test_ensure_question_mark(question, expected):
    assert pipeline.ensure_question_mark(question) == expected


def test_format_chunks():
    assert pipeline.format_chunks(None) == "（无）"
    assert pipeline.format_chunks(SNAP) == "[1] 保温杯 > 清洗（分数 0.31）\n问：怎么清洗\n答：手洗"


async def test_runner_serializes_duplicates(db, use_flywheel, monkeypatch, flywheel_runner):
    emb = FakeEmbeddings(rules=[("洗碗机", [1.0] + [0.0] * 1023)])
    monkeypatch.setattr(pipeline, "get_embeddings", lambda: emb)
    use_flywheel(normalized=[("保温杯洗碗机？", "（待核实）"), ("保温杯洗碗机？", "（待核实）")], dedup=[1])
    a, b = await _lcq(db, "x"), await _lcq(db, "y")
    assert flywheel_runner.submit(a) and flywheel_runner.submit(b)
    await flywheel_runner.drain()
    async with db() as s:
        items = await review_queue.list_items(s, None)
    assert [(i.occurrence_count) for i in items] == [2]


async def test_record_low_confidence_submits(db, monkeypatch):
    submitted = []
    runner = FlywheelRunner()
    monkeypatch.setattr(runner, "submit", submitted.append)
    monkeypatch.setattr(grounding, "get_flywheel_runner", lambda: runner)
    async with db() as s:
        c = await conversations.create(s, "u1")
        await s.commit()
    lcq = await grounding.record_low_confidence(c.id, "q", "r")
    assert submitted == [lcq]


def test_submit_when_not_started_logs(caplog):
    caplog.set_level(logging.INFO)
    assert FlywheelRunner().submit(1) is False
    assert "flywheel_runner_off" in caplog.text


@pytest.mark.parametrize("value", [None, "", " null ", "NULL", " none "])
def test_review_dedup_normalizes_null_like(value):
    from app.schemas import ReviewDedup

    assert ReviewDedup(duplicate_of=value).duplicate_of is None


@pytest.mark.parametrize("name, inputs, expected_json", [
    ("normalize_prompt", {"question": "杯子能机洗吗？", "chunks": "（无）"},
     '{"normalized_question": "...", "suggested_answer": "..."}'),
    ("review_dedup_prompt", {"question": "杯子能机洗吗？", "candidates": "1. 杯子能机洗吗？"},
     '{"duplicate_of": 序号或 null}'),
])
def test_flywheel_prompt_renders_literal_json(name, inputs, expected_json):
    from app import prompts

    messages = getattr(prompts, name).invoke(inputs).to_messages()
    assert expected_json in messages[0].content
    assert "杯子能机洗吗？" in messages[1].content
