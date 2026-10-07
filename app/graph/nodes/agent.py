"""主力 Agent：ReAct 循环的两个节点。回边由 builder 连接。"""

import logging

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.config import AGENT_MAX_STEPS, AGENT_TOKEN_BUDGET, TOKEN_BUDGET
from app.context import build_history, count_tokens
from app.graph import events
from app.graph.control import actions_from_args
from app.prompts import TOOL_ROUND_CLOSING, render_agent_system
from app.services.grounding import Citation, format_evidence
from app.tools.registry import get_registry

logger = logging.getLogger(__name__)

AGENT_TOOLS = ("query_order", "query_logistics", "query_product", "offer_human_options")
TOOL_MARKUP_PREFIX = "<｜"
TOOL_MARKUP_MARKERS = ("<｜", "｜DSML｜", "invoke name=")


class AgentOutputError(RuntimeError):
    """模型输出为空，或含工具调用标记。"""


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
    system = render_agent_system(ctx.today, format_evidence(citations) if citations else "")
    # 历史预算不计证据段，与 ch04 不计工具结果一致。
    history = build_history(
        state.get("messages", []), render_agent_system(ctx.today), state["resolved_input"], TOKEN_BUDGET)
    prompt = [SystemMessage(system), *history, HumanMessage(state["resolved_input"]),
              *state.get("agent_messages", [])]
    force = state.get("force_final", False)
    if force:
        runnable = ctx.model
        prompt = [*prompt, SystemMessage(TOOL_ROUND_CLOSING)]
    else:
        runnable = ctx.model.bind_tools(get_registry().tools_for_model(AGENT_TOOLS), tool_choice="auto")

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
        update["reply"] = text
    return update


async def agent_tools(state, runtime):
    trace = events.enter("agent_tools", state, runtime)
    ctx = runtime.context
    calls = state["agent_messages"][-1].tool_calls
    events.emit("tool_start", {"tools": [{"id": c["id"], "name": c["name"], "args": c["args"]} for c in calls]})
    outcomes = await ctx.execute(calls, conversation_id=ctx.conversation_id)
    events.emit("tool_end", {"tools": [{"id": o.call_id, "name": o.name, "ok": o.ok} for o in outcomes]})
    steps = state.get("steps", 0) + 1
    update = {
        "agent_messages": [*state["agent_messages"], *(o.message for o in outcomes)],
        "steps": steps,
        "trace": trace,
    }
    actions = None
    for outcome, call in zip(outcomes, calls):
        if outcome.name == "offer_human_options" and outcome.ok:
            actions = actions_from_args(call["args"])
    if actions is not None:
        update["actions"] = actions
        events.emit("actions", {"options": actions})
    reason = None
    if steps >= AGENT_MAX_STEPS:
        reason = "steps"
    elif state.get("tokens_used", 0) >= AGENT_TOKEN_BUDGET:
        reason = "tokens"
    if reason:
        logger.info("agent_limit reason=%s conversation=%s steps=%s tokens=%s",
                    reason, ctx.conversation_id, steps, state.get("tokens_used", 0))
        update["force_final"] = True
    return update
