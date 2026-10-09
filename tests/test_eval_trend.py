from datetime import datetime, timedelta

from evals.eval_trend import RunPoint, render_trend, trend

T0 = datetime(2026, 10, 9, 10)


def m(**kw):
    base = {"recall_at_1": 0.8, "recall_at_3": 0.9, "recall_at_5": 0.92, "recall_at_10": 0.95, "mrr": 0.85,
            "faithfulness": 0.97, "false_refusal": 0.06, "d_refusal": 0.9}
    return {**base, **kw}


def test_trend_marks_drops_both_directions():
    runs = [RunPoint(1, T0, "手动", 300, m()), RunPoint(2, T0 + timedelta(hours=1), "定时", 300,
                                                       m(mrr=0.80, false_refusal=0.10, faithfulness=0.98))]
    t = trend(runs, tolerance=0.02)
    assert t.deltas[0] == {k: None for k in t.deltas[0]}
    assert round(t.deltas[1]["mrr"], 2) == -0.05
    assert t.dropped == ["mrr", "false_refusal"]


def test_trend_only_compares_same_size():
    runs = [RunPoint(1, T0, "手动", 300, m()), RunPoint(2, T0 + timedelta(hours=1), "手动", 5, m(mrr=0.1)),
            RunPoint(3, T0 + timedelta(hours=2), "手动", 300, m(mrr=0.84))]
    t = trend(runs)
    assert [p.id for p in t.points] == [1, 3] and t.dropped == []


def test_trend_last_n():
    runs = [RunPoint(i, T0 + timedelta(hours=i), "定时", 300, m()) for i in range(12)]
    assert [p.id for p in trend(runs, last=3).points] == [9, 10, 11]


def test_render_trend():
    runs = [RunPoint(1, T0, "手动", 300, m()), RunPoint(2, T0 + timedelta(hours=1), "定时", 300, m(mrr=0.7))]
    text = render_trend(trend(runs))
    assert "↓" in text and "下滑指标：mrr" in text

