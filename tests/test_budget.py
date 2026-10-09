import logging

import pytest

from app.config import Settings
from app.context.budget import budget_from_settings, compute_budget, startup_check

DEMO = dict(window=18000, max_output=2000, max_user_input=2000, max_agent_steps=3,
            tool_result_max=1200, top_k=5)
DEFAULTS = dict(max_output=8192, max_user_input=1000, max_agent_steps=4,
                tool_result_max=750, top_k=10)


def test_demo_config_gives_5650():
    b = compute_budget(**DEMO)
    assert (b.margin, b.evidence, b.peak) == (900, 1250, 5900)
    assert (b.history, b.layer1, b.layer2) == (5650, 3954, 1695)


def test_window_only_change_gives_zero():
    b = compute_budget(window=18000, **DEFAULTS)
    assert b.avail == -292
    assert (b.history, b.layer1, b.layer2) == (0, 0, 0)


def test_default_window_is_capped_by_keep_turns(monkeypatch):
    import app.config as config

    monkeypatch.setattr(config, "TURN_TOKENS", 800)
    b = compute_budget(window=128000, **DEFAULTS)
    assert b.avail == 104208
    assert (b.history, b.layer1, b.layer2) == (24000, 16800, 7200)


def test_budget_from_settings_reads_env(monkeypatch):
    for key, value in {
        "MODEL_CONTEXT_WINDOW": "18000", "MAX_OUTPUT_TOKENS": "2000",
        "MAX_USER_INPUT_TOKENS": "2000", "MAX_AGENT_STEPS": "3",
        "TOOL_RESULT_MAX_TOKENS": "1200", "RERANK_TOP_K": "5",
    }.items():
        monkeypatch.setenv(key, value)
    assert budget_from_settings(Settings()).history == 5650


def test_startup_check_warns_when_budget_is_short(caplog):
    caplog.set_level(logging.INFO)
    startup_check(compute_budget(window=18000, **DEFAULTS), measured_system=1000)
    assert "budget window=18000" in caplog.text
    assert "上下文预算不足" in caplog.text


def test_startup_check_warns_when_system_reserve_exceeded(caplog):
    caplog.set_level(logging.INFO)
    startup_check(compute_budget(**DEMO), measured_system=2500)
    assert "system_reserve_exceeded measured=2500 reserve=1800" in caplog.text
    assert "上下文预算不足" not in caplog.text


@pytest.mark.parametrize("window, warns", [(19509, True), (19510, False)])
def test_startup_check_layer1_must_fit_one_turn(window, warns, caplog):
    caplog.set_level(logging.INFO)
    budget = compute_budget(window=window, **DEFAULTS)
    assert budget.history > 0
    startup_check(budget, measured_system=1800)
    assert ("上下文预算不足" in caplog.text) is warns
    assert "system_reserve_exceeded" not in caplog.text
