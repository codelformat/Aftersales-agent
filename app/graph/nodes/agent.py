"""主力 Agent：ReAct 循环的两个节点。回边由 builder 连接。"""

import json
import logging
import math

from langchain_core.messages import AIMessage, SystemMessage

from app.config import AGENT_TOKEN_BUDGET, TOOL_SCHEMA_CHARS_PER_TOKEN, get_settings
from app.context import count_tokens
from app.context.assemble import build_agent_prompt, log_model_ctx
from app.graph import events
from app.graph.control import actions_from_args, refund_action
from app.prompts import (
    AFTERSALES_TASK_POLICY, AFTERSALES_TASK_WITH_ORDER, TOOL_ROUND_CLOSING, format_order, render_agent_system,
    render_reference,
)
from app.services.grounding import Citation, format_evidence, parse_citations
from app.tools.executor import failure_outcome
from app.tools.toolset import Toolset, builtin_toolset

logger = logging.getLogger(__name__)

AGENT_TOOLS = ("query_order", "query_logistics", "query_product", "offer_human_options")
REFUND_FORM_TOOL = "offer_refund_form"
TOOL_MARKUP_PREFIX = "<｜"
TOOL_MARKUP_MARKERS = ("<｜", "｜DSML｜", "invoke name=")


def measure_system_tokens() -> int:
    """估算 System Prompt 和全部 Agent 工具定义的 token。"""
    base = builtin_toolset()
    schema = json.dumps([base.get(name).openai_tool() for name in (*AGENT_TOOLS, REFUND_FORM_TOOL)],
                        ensure_ascii=False)
    return count_tokens([SystemMessage(render_agent_system())]) + math.ceil(len(schema) / TOOL_SCHEMA_CHARS_PER_TOKEN)


class AgentOutputError(RuntimeError):
    """模型输出为空，或含工具调用标记。"""


def turn_toolset(state, base: Toolset) -> Toolset:
    """按本轮状态收窄工具集。"""
    entries = dict(base.entries)
    closed = dict(base.closed)
    for name, entry in list(entries.items()):
        if not entry.agent or name not in (*AGENT_TOOLS, REFUND_FORM_TOOL):
            closed[name] = "工具未开放"
            del entries[name]
    if not (state.get("route") == "aftersales" and state.get("order_id")):
        if REFUND_FORM_TOOL in entries:
            closed[REFUND_FORM_TOOL] = "工具未开放"
            del entries[REFUND_FORM_TOOL]
    entries = {name: entries[name] for name in (*AGENT_TOOLS, REFUND_FORM_TOOL) if name in entries}
    return Toolset(entries, closed, base.unavailable)


def _aftersales_sections(state) -> tuple[str, str]:
    if state.get("route") != "aftersales":
        return "", ""
    if state.get("order_scoped"):
        return format_order(state.get("order")), AFTERSALES_TASK_WITH_ORDER
    return "", AFTERSALES_TASK_POLICY


def _count_turn_tokens(gathered, prompt) -> int:
    usage = getattr(gathered, "usage_metadata", None) or {}
    if usage.get("total_tokens"):
        return usage["total_tokens"]
    output = AIMessage(content=gathered.content or "", tool_calls=gathered.tool_calls) if gathered else AIMessage("")
    return count_tokens([*prompt, output])


async def agent_model(state, runtime):
    trace = events.enter("agent_model", state, runtime)
    ctx = runtime.context
    citations = [Citation(**c) for c in state.get("evidence", [])]
    order_section, task_section = _aftersales_sections(state)
    reference = render_reference(ctx.today, state.get("summary") or "", order_section, task_section,
                                 format_evidence(citations) if citations else "")
    built = build_agent_prompt(
        history=state.get("messages", []), summary_upto=state.get("summary_upto"),
        layer1_from=state.get("layer1_from"), summary=state.get("summary"),
        user_input=state["resolved_input"], reference=reference, agent_messages=state.get("agent_messages", []))
    prompt = built.messages
    force = state.get("force_final", False)
    if force:
        runnable = ctx.model
        prompt.append(SystemMessage(TOOL_ROUND_CLOSING))
    else:
        runnable = ctx.model.bind_tools(
            turn_toolset(state, ctx.base_toolset if ctx.base_toolset is not None else builtin_toolset()).agent_tools(),
            tool_choice="auto")
    log_model_ctx(ctx.conversation_id, state.get("steps", 0), built)

    text = ""
    gathered = None
    pending: list[str] = []
    prefix_checked = False
    try:
        async for chunk in runnable.astream(prompt):
            gathered = chunk if gathered is None else gathered + chunk
            if not (isinstance(chunk.content, str) and chunk.content):
                continue
            text += chunk.content
            if prefix_checked:
                events.emit("token", {"text": chunk.content})
                continue
            # 先缓冲前两个非空白字符，以工具调用标记开头时不流出。
            pending.append(chunk.content)
            if len(text.lstrip()) < 2:
                continue
            if text.lstrip().startswith(TOOL_MARKUP_PREFIX):
                break
            prefix_checked = True
            for token in pending:
                events.emit("token", {"text": token})
            pending.clear()
    except ValueError as exc:
        # LangChain 在空流结束时先抛出异常，统一转为 Agent 输出错误。
        if gathered is None and str(exc) == "No generation chunks were returned":
            raise AgentOutputError("模型返回空回复") from exc
        raise

    if text.lstrip().startswith(TOOL_MARKUP_PREFIX) or any(m in text for m in TOOL_MARKUP_MARKERS):
        raise AgentOutputError("模型输出含工具调用标记")
    for token in pending:
        events.emit("token", {"text": token})
    tool_calls = gathered.tool_calls if gathered is not None else []
    if force and tool_calls:
        raise AgentOutputError("强制收尾时出现工具调用")
    update = {
        "agent_messages": [*state.get("agent_messages", []), AIMessage(content=text, tool_calls=tool_calls)],
        "tokens_used": state.get("tokens_used", 0) + _count_turn_tokens(gathered, prompt),
        "trace": trace,
    }
    if not tool_calls:
        if not text:
            raise AgentOutputError("模型返回空回复")
        invalid_citations = [n for n in parse_citations(text) if not 1 <= n <= len(citations)]
        if invalid_citations:
            logger.warning("回复含越界引用编号：%s", invalid_citations)
        update["reply"] = text
    return update


async def agent_tools(state, runtime):
    trace = events.enter("agent_tools", state, runtime)
    ctx = runtime.context
    calls = state["agent_messages"][-1].tool_calls
    events.emit("tool_start", {"tools": [{"id": c["id"], "name": c["name"], "args": c["args"]} for c in calls]})
    toolset = turn_toolset(state, ctx.base_toolset if ctx.base_toolset is not None else builtin_toolset())
    outcomes_by_id = {}
    runnable_calls = []
    for call in calls:
        if (call["name"] == REFUND_FORM_TOOL and toolset.get(REFUND_FORM_TOOL) is not None
                and call["args"].get("order_id") != state.get("order_id")):
            logger.warning("退款单订单号不符：%s 会话=%s", call["args"].get("order_id"), ctx.conversation_id)
            outcomes_by_id[call["id"]] = failure_outcome(call["id"], call["name"], "invalid_order")
        else:
            runnable_calls.append(call)
    if runnable_calls:
        for outcome in await ctx.execute(runnable_calls, conversation_id=ctx.conversation_id, toolset=toolset):
            outcomes_by_id[outcome.call_id] = outcome
    outcomes = [outcomes_by_id[c["id"]] for c in calls]
    events.emit("tool_end", {"tools": [{"id": o.call_id, "name": o.name, "ok": o.ok} for o in outcomes]})
    steps = state.get("steps", 0) + 1
    update = {
        "agent_messages": [*state["agent_messages"], *(o.message for o in outcomes)],
        "steps": steps,
        "trace": trace,
    }
    actions = []
    for outcome, call in zip(outcomes, calls):
        if not outcome.ok:
            continue
        if outcome.name == "offer_human_options":
            actions += actions_from_args(call["args"])
        elif outcome.name == REFUND_FORM_TOOL:
            actions.append(refund_action(call["args"]["order_id"]))
    if actions:
        update["actions"] = actions
        events.emit("actions", {"options": actions})
    reason = None
    if steps >= get_settings().max_agent_steps:
        reason = "steps"
    elif state.get("tokens_used", 0) >= AGENT_TOKEN_BUDGET:
        reason = "tokens"
    if reason:
        logger.info("agent_limit reason=%s conversation=%s steps=%s tokens=%s",
                    reason, ctx.conversation_id, steps, state.get("tokens_used", 0))
        update["force_final"] = True
    return update
