import pytest

from evals import run_eval_pipeline as pipe
from evals.rag_metrics import GenResult, RetrievalScore
from evals.eval_trend import build_metrics

pytestmark = pytest.mark.anyio


def test_build_metrics():
    scores = [RetrievalScore("A1", "A_policy", "easy", "hybrid_rerank", {1: 1.0, 3: 1.0, 5: 1.0, 10: 1.0}, 1.0),
              RetrievalScore("A2", "A_policy", "easy", "hybrid_rerank", {1: 0.0, 3: 1.0, 5: 1.0, 10: 1.0}, 0.5)]
    results = [GenResult("A1", "A_policy", "easy", "hybrid_rerank", "q", True, False, "a", [], True, [], ""),
               GenResult("D1", "D_unanswerable", "easy", "hybrid_rerank", "q", True, True, "抱歉", [], None, [], "")]
    assert pipe.build_metrics is build_metrics
    got = build_metrics(scores, results, failures=2)
    assert got["recall_at_1"] == 0.5 and got["mrr"] == 0.75 and got["faithfulness"] == 1.0
    assert got["d_refusal"] == 1.0 and got["false_refusal"] == 0.0 and got["failures"] == 2
    assert got["strategy"] == "hybrid_rerank" and "gate_conf_threshold" in got


async def test_save_run_writes_row(db):
    run_id = await pipe.save_run("定时", 300, {"mrr": 0.8})
    from app.repositories import eval_runs
    async with db() as s:
        [row] = await eval_runs.list_recent(s, 5)
    assert (row.id, row.triggered_by, row.dataset_size, row.metrics) == (run_id, "定时", 300, {"mrr": 0.8})


def test_stage_failure_detection():
    assert pipe.whole_stage_failed(scores=[], results=[1]) is True
    assert pipe.whole_stage_failed(scores=[1], results=[]) is True
    assert pipe.whole_stage_failed(scores=[1], results=[1]) is False

