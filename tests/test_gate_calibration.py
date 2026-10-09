from itertools import product

import pytest

from evals.gate_calibration import (
    Params, SignalRow, cross_validate, evaluate, search, stratified_folds, weight_grid,
)


def rows():
    out = []
    for i in range(20):
        out.append(SignalRow(f"A{i}", "A_policy", True, [0.8, 0.3, 0.25]))
    for i in range(10):
        out.append(SignalRow(f"D{i}", "D_unanswerable", False, [0.45, 0.44]))  # 分数高但分差小
    return out


def test_weight_grid():
    grid = weight_grid(0.5)
    assert set(grid) == {(1.0, 0.0, 0.0), (0.5, 0.5, 0.0), (0.5, 0.0, 0.5), (0.0, 1.0, 0.0), (0.0, 0.5, 0.5),
                         (0.0, 0.0, 1.0)}
    assert len(weight_grid(0.1)) == 66


def test_evaluate_old_rule_cannot_reject_d():
    o = evaluate(rows(), Params((1.0, 0.0, 0.0), 3, 0.20))
    assert (o.keep, o.d_reject) == (1.0, 0.0)


def test_search_finds_margin_weight():
    best = search(rows(), ns=(3,), thresholds=[i / 100 for i in range(0, 101)])
    assert best.keep >= 0.95 and best.d_reject == 1.0
    assert best.params.weights[2] > 0


def test_unrelevant_top1_not_kept():
    r = [SignalRow("A1", "A_policy", False, [0.9])]
    assert evaluate(r, Params((1, 0, 0), 3, 0.1)).keep == 0.0


def test_stratified_folds_cover_all_once():
    folds = stratified_folds(rows(), k=5, seed=0)
    ids = [r.sample_id for f in folds for r in f]
    assert sorted(ids) == sorted(r.sample_id for r in rows())
    assert all(sum(r.bucket == "D_unanswerable" for r in f) == 2 for f in folds)


def test_cross_validate_shapes():
    res = cross_validate(rows(), k=5, seed=0)
    assert len(res) == 5 and all(train.params == test.params for train, test in res)


def test_evaluate_empty_and_minimum_evidence():
    params = Params((1.0, 0.0, 0.0), 3, 0.0)
    assert (evaluate([], params).keep, evaluate([], params).d_reject) == (0.0, 0.0)
    signals = [SignalRow("A1", "A_policy", True, [0.19]),
               SignalRow("D1", "D_unanswerable", False, [])]
    outcome = evaluate(signals, params)
    assert (outcome.keep, outcome.d_reject) == (0.0, 1.0)


@pytest.mark.parametrize("min_keep", [0.0, 0.5, 0.95])
def test_search_matches_exhaustive_evaluation(min_keep):
    signals = [SignalRow("A1", "A_policy", True, [0.6, 0.2]),
               SignalRow("B1", "B_model", True, [0.2]),
               SignalRow("C1", "C_colloquial", False, [0.9, 0.4]),
               SignalRow("E1", "E_multi", True, []),
               SignalRow("D1", "D_unanswerable", False, [0.4, 0.4]),
               SignalRow("D2", "D_unanswerable", False, [0.19]),
               SignalRow("D3", "D_unanswerable", False, [])]
    thresholds = (0.0, 0.2, 0.4, 0.6, 1.0)
    outcomes = [evaluate(signals, Params(w, n, t))
                for w, n, t in product(weight_grid(), (2, 3), thresholds)]
    feasible = [o for o in outcomes if o.keep >= min_keep]
    expected = min(feasible, key=lambda o: (
        -o.d_reject, -o.keep, o.params.threshold, o.params.effective_n,
        tuple(-w for w in o.params.weights)), default=None)
    assert search(signals, ns=(2, 3), thresholds=thresholds, min_keep=min_keep) == expected


def test_search_ties_are_deterministic():
    signals = [SignalRow("A1", "A_policy", True, [1.0])]
    assert search(signals, ns=(5, 2), thresholds=(0.2, 0.0)).params == Params((1.0, 0.0, 0.0), 2, 0.0)


def test_folds_reproducible_without_mutating_rows():
    signals = rows()
    original = signals.copy()
    assert stratified_folds(signals, seed=0) == stratified_folds(list(reversed(signals)), seed=0)
    assert signals == original
