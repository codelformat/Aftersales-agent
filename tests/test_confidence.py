import pytest

from app.services.confidence import Confidence, evidence_confidence, gate_passes


def test_empty():
    c = evidence_confidence([], weights=(0.5, 0.3, 0.2), effective_n=3, min_score=0.2)
    assert c == Confidence(0.0, 0.0, 0.0, 0.0)


def test_single():
    c = evidence_confidence([0.6], weights=(0.5, 0.3, 0.2), effective_n=3, min_score=0.2)
    assert (c.top1, c.margin) == (0.6, 0.6)
    assert c.effective == pytest.approx(1 / 3)
    assert c.score == pytest.approx(0.5 * 0.6 + 0.3 / 3 + 0.2 * 0.6)


def test_effective_caps_and_ignores_low():
    c = evidence_confidence([0.9, 0.5, 0.3, 0.25, 0.1], weights=(0, 1, 0), effective_n=3, min_score=0.2)
    assert c.effective == 1.0 and c.score == 1.0
    c2 = evidence_confidence([0.9, 0.1], weights=(0, 1, 0), effective_n=2, min_score=0.2)
    assert c2.effective == 0.5


def test_ties_margin_zero():
    c = evidence_confidence([0.4, 0.4], weights=(0, 0, 1), effective_n=3, min_score=0.2)
    assert c.margin == 0.0 and c.score == 0.0


def test_unsorted_input_is_sorted():
    c = evidence_confidence([0.1, 0.7, 0.3], weights=(1, 0, 0), effective_n=3, min_score=0.2)
    assert (c.top1, c.margin) == (0.7, pytest.approx(0.4))


def test_gate_passes_needs_evidence_above_min_score():
    ok, _ = gate_passes([0.15], weights=(0, 0, 1), effective_n=3, threshold=0.1, min_score=0.2)
    assert ok is False
    ok, c = gate_passes([0.5, 0.1], weights=(1, 0, 0), effective_n=3, threshold=0.2, min_score=0.2)
    assert ok is True and c.score == 0.5


def test_old_rule_params():
    assert gate_passes([0.2], weights=(1, 0, 0), effective_n=3, threshold=0.20)[0] is True
    assert gate_passes([0.19], weights=(1, 0, 0), effective_n=3, threshold=0.20)[0] is False


def test_calibrated_defaults():
    ok, c = gate_passes([0.8, 0.3])
    assert ok is True and c.score == pytest.approx(0.71)
    # [0.45, 0.44] 得分 0.395，会通过；用 0.388 的同分证据验证拒答。
    ok, c = gate_passes([0.44, 0.44])
    assert ok is False and c.score == pytest.approx(0.388)


def test_to_dict_rounds():
    assert Confidence(0.123456, 1 / 3, 0.0, 0.5).to_dict() == {
        "top1": 0.1235, "effective": 0.3333, "margin": 0.0, "score": 0.5}
