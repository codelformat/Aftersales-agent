import asyncio

from langchain_core.runnables import RunnableLambda
import pytest

from evals import run_normalize_eval

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("question, chunks, answer, expected", [
    ("K1 能退吗", [{"answer": "签收 7 天内可退。"}], "（待核实）K1 是否可退需核实。", True),
    ("K1 能退吗", [{"answer": "签收 7 天内可退。"}], "（待核实）签收 7 天内可退。", True),
    ("K1 能退吗", [{"answer": "签收 7 天内可退。"}], "（待核实）签收 8 天内可退。", False),
    ("K1 能退吗", None, "（待核实）K1 是否可退需核实。", True),
    ("K1 能退吗", None, "（待核实）签收 8 天内可退。", False),
])
async def test_answer_numbers_require_original_evidence(monkeypatch, question, chunks, answer, expected):
    if chunks:
        chunks = [dict(chunk, section_path="退货政策", question="能退吗", score=0.42) for chunk in chunks]
    sample = {"question": question, "chunks": chunks, "must_keep": [], "must_drop": []}
    normalizer = RunnableLambda(lambda _: {
        "parsed": {"normalized_question": "能退吗？", "suggested_answer": answer},
    })
    monkeypatch.setattr(run_normalize_eval, "get_question_normalizer", lambda: normalizer)

    passed, parsed, line = await run_normalize_eval.evaluate_sample(1, sample, asyncio.Semaphore(1))

    assert parsed is True
    assert passed is expected, line


async def test_eval_checks_normalized_question_after_punctuation_cleanup(monkeypatch):
    sample = {"question": "能开专票吗", "chunks": None, "must_keep": ["专票"], "must_drop": []}
    normalizer = RunnableLambda(lambda _: {
        "parsed": {"normalized_question": "能开专票吗?", "suggested_answer": "（待核实）需核实。"},
    })
    monkeypatch.setattr(run_normalize_eval, "get_question_normalizer", lambda: normalizer)

    passed, parsed, line = await run_normalize_eval.evaluate_sample(1, sample, asyncio.Semaphore(1))

    assert parsed is True
    assert passed is True, line
    assert "问题：能开专票吗？" in line
