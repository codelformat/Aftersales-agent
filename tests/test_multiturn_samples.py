import asyncio
import json
from pathlib import Path

import pytest

from app.schemas import INTENTS
from evals import run_multiturn_eval

SAMPLES_PATH = Path(__file__).resolve().parent.parent / "evals" / "multiturn_samples.jsonl"


def test_multiturn_samples_shape():
    samples = [json.loads(line) for line in SAMPLES_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(samples) == 10
    assert {sample["id"] for sample in samples} == {"m1", "m2", "m3", "m4", "m5", "m6", "m7", "ticket-1", "ticket-2", "status-1"}
    followup_groups = 0
    for sample in samples:
        assert set(sample) == {"id", "turns"}
        assert isinstance(sample["id"], str) and sample["id"].strip()
        turns = sample["turns"]
        assert isinstance(turns, list) and len(turns) >= 3
        if sample["id"].startswith("ticket-"):
            assert len(turns) == 3
            expected = [True, True, False] if sample["id"] == "ticket-1" else [False, False, True]
            assert [turn["expect_ticket_request"] for turn in turns] == expected
        elif sample["id"] == "status-1":
            assert [turn["expect_status_query"] for turn in turns] == [True, False, True, False]
        elif sample["id"] != "m7":
            assert [turn["intent"] for turn in turns] == ["物流", *["退款退货"] * (len(turns) - 2), "物流"]
        else:
            assert len(turns) == 4
            assert [turn["history_recall"] for turn in turns] == [False, False, False, True]
            assert [turn.get("intent") for turn in turns] == ["退款退货", None, "商品咨询", None]
        followup_groups += len(turns) >= 4
        for i, turn in enumerate(turns):
            assert "user" in turn
            assert set(turn) <= {"user", "assistant", "intent", "unchanged", "must_contain_any", "history_recall",
                                 "expect_ticket_request", "expect_status_query"}
            assert isinstance(turn["user"], str) and turn["user"].strip()
            if "intent" in turn:
                assert turn["intent"] in INTENTS
            if "history_recall" in turn:
                assert isinstance(turn["history_recall"], bool)
            if "expect_status_query" in turn:
                assert isinstance(turn["expect_status_query"], bool)
            if "expect_ticket_request" in turn:
                assert isinstance(turn["expect_ticket_request"], bool)
            assert ("unchanged" in turn) != ("must_contain_any" in turn)
            if "unchanged" in turn:
                assert turn["unchanged"] is True
            else:
                words = turn["must_contain_any"]
                assert isinstance(words, list) and words
                assert all(isinstance(word, str) and word.strip() for word in words)
            if i < len(turns) - 1:
                assert "assistant" in turn
            if "assistant" in turn:
                assert isinstance(turn["assistant"], str) and turn["assistant"].strip()
    assert followup_groups >= 2


@pytest.mark.anyio
@pytest.mark.parametrize("actual, expected, errors", [
    (True, True, []), (False, False, []),
    (False, True, ["工单请求错"]), (True, False, ["工单请求错"]),
])
async def test_eval_checks_ticket_request_without_intent(use_resolver, use_intent, actual, expected, errors):
    use_resolver({"ticket_request": actual})
    use_intent("其他")
    sample = {"id": "test", "turns": [
        {"user": "帮我建个工单", "unchanged": True, "expect_ticket_request": expected},
    ]}
    results = await run_multiturn_eval.evaluate_group(sample, asyncio.Semaphore(1))
    assert results[0][2] == errors


@pytest.mark.anyio
async def test_eval_skips_ticket_request_when_unmarked(use_resolver, use_intent):
    use_resolver({"ticket_request": True})
    use_intent("其他")
    sample = {"id": "test", "turns": [{"user": "帮我建个工单", "unchanged": True}]}
    results = await run_multiturn_eval.evaluate_group(sample, asyncio.Semaphore(1))
    assert results[0][2] == []


@pytest.mark.anyio
@pytest.mark.parametrize("actual, expected, errors", [(True, True, []), (False, True, ["回顾错"]),
                                                    (True, False, ["回顾错"])])
async def test_eval_checks_history_recall_without_intent(use_resolver, use_intent, actual, expected, errors):
    use_resolver({}, {"history_recall": actual})
    use_intent("闲聊", "售后")
    sample = {"id": "test", "turns": [
        {"user": "你好", "assistant": "您好", "intent": "闲聊", "unchanged": True},
        {"user": "你之前说什么", "unchanged": True, "history_recall": expected},
    ]}
    results = await run_multiturn_eval.evaluate_group(sample, asyncio.Semaphore(1))
    assert results[0][2] == []
    assert results[1][2] == errors


@pytest.mark.anyio
async def test_run_eval_reports_turn_without_intent(tmp_path, monkeypatch, use_resolver, use_intent, capsys):
    sample = {"id": "test", "turns": [{"user": "你好", "unchanged": True, "history_recall": False}]}
    path = tmp_path / "samples.jsonl"
    path.write_text(json.dumps(sample, ensure_ascii=False) + "\n", encoding="utf-8")
    monkeypatch.setattr(run_multiturn_eval, "SAMPLES_PATH", path)
    use_resolver({})
    use_intent("闲聊")
    assert await run_multiturn_eval.run_eval() == 0
    assert "通过轮数：1/1" in capsys.readouterr().out


@pytest.mark.anyio
@pytest.mark.parametrize("actual, expected, errors", [
    (True, True, []), (False, False, []),
    (False, True, ["状态查询错"]), (True, False, ["状态查询错"]),
])
async def test_eval_checks_status_query(use_resolver, use_intent, actual, expected, errors):
    use_resolver({"status_query": actual})
    use_intent("其他")
    sample = {"id": "test", "turns": [
        {"user": "退款退到哪了", "unchanged": True, "expect_status_query": expected},
    ]}
    results = await run_multiturn_eval.evaluate_group(sample, asyncio.Semaphore(1))
    assert results[0][2] == errors


@pytest.mark.anyio
async def test_eval_skips_status_query_when_unmarked(use_resolver, use_intent):
    use_resolver({"status_query": True})
    use_intent("其他")
    sample = {"id": "test", "turns": [{"user": "退款退到哪了", "unchanged": True}]}
    results = await run_multiturn_eval.evaluate_group(sample, asyncio.Semaphore(1))
    assert results[0][2] == []
