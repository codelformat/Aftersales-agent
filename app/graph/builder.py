from contextlib import asynccontextmanager
from functools import wraps
from pathlib import Path
import time

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.errors import GraphInterrupt
from langgraph.graph import END, START, StateGraph

from app.config import CHECKPOINT_DB_PATH, GRAPH_RECURSION_LIMIT
from app.graph import events
from app.graph.nodes.agent import agent_model, agent_tools
from app.graph.nodes.aftersales import ensure_order, expand_query, fetch_order, retrieve_multi_evidence
from app.graph.nodes.confirm import confirm_write, ticket_reply
from app.graph.nodes.finalize import finalize
from app.graph.nodes.intent import classify_intent
from app.graph.nodes.knowledge import confidence_gate, retrieve_evidence
from app.graph.nodes.replies import chitchat_reply, complaint_reply, fallback_reply
from app.graph.nodes.turn import resolve_reference, start_turn
from app.graph.routing import after_agent, after_gate, after_intent, after_tools
from app.graph.state import ChatState, GraphContext
from app.observability import TRACE_NAME, get_langfuse_handler

_graph = None


def timed(name, fn):
    """统一记录节点耗时；interrupt 保留原异常供 LangGraph 处理。"""
    @wraps(fn)
    async def run(state, runtime):
        started_at = time.monotonic()
        events.trace("node_start", {}, node=name)
        try:
            result = await fn(state, runtime)
        except GraphInterrupt:
            events.trace("node_end", {"ms": int((time.monotonic() - started_at) * 1000),
                                      "interrupted": True}, node=name)
            raise
        events.trace("node_end", {"ms": int((time.monotonic() - started_at) * 1000)}, node=name)
        return result

    return run


def build_graph(checkpointer, callbacks: list | None = None):
    g = StateGraph(ChatState, context_schema=GraphContext)
    for name, fn in (
        ("start_turn", start_turn), ("resolve_reference", resolve_reference),
        ("classify_intent", classify_intent), ("retrieve", retrieve_evidence),
        ("ensure_order", ensure_order), ("fetch_order", fetch_order), ("expand_query", expand_query),
        ("retrieve_multi", retrieve_multi_evidence),
        ("confidence_gate", confidence_gate), ("agent_model", agent_model),
        ("agent_tools", agent_tools), ("fallback_reply", fallback_reply),
        ("confirm_write", confirm_write), ("ticket_reply", ticket_reply),
        ("complaint_reply", complaint_reply), ("chitchat_reply", chitchat_reply),
        ("finalize", finalize),
    ):
        g.add_node(name, timed(name, fn))
    g.add_edge(START, "start_turn")
    g.add_edge("start_turn", "resolve_reference")
    g.add_edge("resolve_reference", "classify_intent")
    g.add_conditional_edges("classify_intent", after_intent, {
        "knowledge": "retrieve", "business": "agent_model",
        "complaint": "complaint_reply", "chitchat": "chitchat_reply",
        "ensure_order": "ensure_order", "expand_query": "expand_query",
    })
    g.add_edge("ensure_order", "fetch_order")
    g.add_edge("fetch_order", "expand_query")
    g.add_edge("expand_query", "retrieve_multi")
    g.add_edge("retrieve_multi", "confidence_gate")
    g.add_edge("retrieve", "confidence_gate")
    g.add_conditional_edges("confidence_gate", after_gate, ["agent_model", "fallback_reply"])
    g.add_conditional_edges("agent_model", after_agent, ["agent_tools", "confirm_write", "finalize"])
    g.add_edge("confirm_write", "agent_tools")
    g.add_conditional_edges("agent_tools", after_tools, ["agent_model", "ticket_reply"])
    for name in ("fallback_reply", "complaint_reply", "chitchat_reply", "ticket_reply"):
        g.add_edge(name, "finalize")
    g.add_edge("finalize", END)
    compiled = g.compile(checkpointer=checkpointer)
    # 编译时挂一次回调，不在每次请求时传入。
    return compiled.with_config({"callbacks": callbacks}) if callbacks else compiled


def get_graph():
    if _graph is None:
        raise RuntimeError("图未初始化")
    return _graph


def set_graph(graph) -> None:
    global _graph
    _graph = graph


def thread_config(conversation_id: int, user_id: str | None = None, intent: str | None = None) -> dict:
    metadata = {"langfuse_session_id": str(conversation_id), "langfuse_trace_name": TRACE_NAME}
    if user_id:
        metadata["langfuse_user_id"] = user_id
    if intent is not None:
        metadata["intent"] = intent
    return {"configurable": {"thread_id": str(conversation_id)}, "recursion_limit": GRAPH_RECURSION_LIMIT,
            "metadata": metadata}


@asynccontextmanager
async def open_graph(path: str = CHECKPOINT_DB_PATH):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    handler = get_langfuse_handler()
    async with AsyncSqliteSaver.from_conn_string(path) as checkpointer:
        graph = build_graph(checkpointer, [handler] if handler else None)
        set_graph(graph)
        try:
            yield graph
        finally:
            set_graph(None)
