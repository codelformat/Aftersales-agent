"""从模型窗口倒推历史预算。"""

import logging
from dataclasses import dataclass

import app.config as config
from app.config import Settings, get_settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ContextBudget:
    window: int
    output: int
    margin: int
    system: int
    evidence: int
    summary: int
    peak: int
    avail: int
    history: int
    layer1: int
    layer2: int


def compute_budget(
    *, window: int, max_output: int, max_user_input: int, max_agent_steps: int,
    tool_result_max: int, top_k: int,
) -> ContextBudget:
    margin = int(window * config.SAFETY_MARGIN_RATIO)
    evidence = top_k * config.EVIDENCE_ITEM_TOKENS
    # 单轮峰值包含用户输入、工具结果和工具调用消息。
    peak = max_user_input + max_agent_steps * (tool_result_max + config.STEP_OVERHEAD_TOKENS)
    avail = (window - max_output - margin - config.SYSTEM_RESERVE_TOKENS - evidence
             - config.SUMMARY_RESERVE_TOKENS - peak)
    history = max(0, min(config.KEEP_TURNS * config.TURN_TOKENS, avail))
    return ContextBudget(
        window=window, output=max_output, margin=margin, system=config.SYSTEM_RESERVE_TOKENS,
        evidence=evidence, summary=config.SUMMARY_RESERVE_TOKENS, peak=peak, avail=avail,
        history=history, layer1=int(history * config.LAYER1_RATIO), layer2=int(history * config.LAYER2_RATIO),
    )


def budget_from_settings(settings: Settings) -> ContextBudget:
    return compute_budget(
        window=settings.model_context_window, max_output=settings.max_output_tokens,
        max_user_input=settings.max_user_input_tokens, max_agent_steps=settings.max_agent_steps,
        tool_result_max=settings.tool_result_max_tokens, top_k=settings.rerank_top_k,
    )


_budget: ContextBudget | None = None


def get_budget() -> ContextBudget:
    global _budget
    if _budget is None:
        _budget = budget_from_settings(get_settings())
    return _budget


def set_budget(budget: ContextBudget | None) -> None:
    global _budget
    _budget = budget


def startup_check(budget: ContextBudget, measured_system: int) -> None:
    b = budget
    logger.info(
        "budget window=%s output=%s margin=%s system=%s evidence=%s summary=%s peak=%s avail=%s "
        "history=%s layer1=%s layer2=%s",
        b.window, b.output, b.margin, b.system, b.evidence, b.summary, b.peak, b.avail,
        b.history, b.layer1, b.layer2,
    )
    if measured_system > b.system:
        logger.warning("system_reserve_exceeded measured=%s reserve=%s", measured_system, b.system)
    if b.history <= 0 or b.layer1 < config.TURN_TOKENS:
        logger.warning("上下文预算不足 history=%s layer1=%s turn_tokens=%s",
                       b.history, b.layer1, config.TURN_TOKENS)
