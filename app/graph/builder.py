from contextlib import asynccontextmanager
from pathlib import Path

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph

from app.config import CHECKPOINT_DB_PATH, GRAPH_RECURSION_LIMIT
from app.graph.nodes.agent import agent_model, agent_tools
from app.graph.nodes.finalize import finalize
from app.graph.nodes.intent import classify_intent
from app.graph.nodes.knowledge import confidence_gate, retrieve_evidence
from app.graph.nodes.replies import chitchat_reply, complaint_reply, fallback_reply
from app.graph.nodes.turn import resolve_reference, start_turn
from app.graph.routing import after_agent, after_gate, after_intent
from app.graph.state import ChatState, GraphContext

_graph = None


def build_graph(checkpointer):
    g = StateGraph(ChatState, context_schema=GraphContext)
    for name, fn in (
        ("start_turn", start_turn), ("resolve_reference", resolve_reference),
        ("classify_intent", classify_intent), ("retrieve", retrieve_evidence),
        ("confidence_gate", confidence_gate), ("agent_model", agent_model),
        ("agent_tools", agent_tools), ("fallback_reply", fallback_reply),
        ("complaint_reply", complaint_reply), ("chitchat_reply", chitchat_reply),
        ("finalize", finalize),
    ):
        g.add_node(name, fn)
    g.add_edge(START, "start_turn")
    g.add_edge("start_turn", "resolve_reference")
    g.add_edge("resolve_reference", "classify_intent")
    g.add_conditional_edges("classify_intent", after_intent, {
        "knowledge": "retrieve", "business": "agent_model",
        "complaint": "complaint_reply", "chitchat": "chitchat_reply",
    })
    g.add_edge("retrieve", "confidence_gate")
    g.add_conditional_edges("confidence_gate", after_gate, ["agent_model", "fallback_reply"])
    g.add_conditional_edges("agent_model", after_agent, ["agent_tools", "finalize"])
    g.add_edge("agent_tools", "agent_model")
    for name in ("fallback_reply", "complaint_reply", "chitchat_reply"):
        g.add_edge(name, "finalize")
    g.add_edge("finalize", END)
    return g.compile(checkpointer=checkpointer)


def get_graph():
    if _graph is None:
        raise RuntimeError("图未初始化")
    return _graph


def set_graph(graph) -> None:
    global _graph
    _graph = graph


def thread_config(conversation_id: int) -> dict:
    return {"configurable": {"thread_id": str(conversation_id)}, "recursion_limit": GRAPH_RECURSION_LIMIT}


@asynccontextmanager
async def open_graph(path: str = CHECKPOINT_DB_PATH):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    async with AsyncSqliteSaver.from_conn_string(path) as checkpointer:
        graph = build_graph(checkpointer)
        set_graph(graph)
        try:
            yield graph
        finally:
            set_graph(None)
