"""分流规则写死在代码中。「其他」走 business，由 Agent 做需求澄清。"""

from app.graph.nodes.agent import TICKET_TOOL

INTENT_ROUTES = {
    "商品咨询": "knowledge",
    "退款退货": "aftersales",
    "售后": "aftersales",
    "物流": "business",
    "订单": "business",
    "其他": "business",
    "投诉": "complaint",
    "闲聊": "chitchat",
}
# 意图识别失败时走 business：Agent 有工具，能处理大多数问题。
FALLBACK_ROUTE = "business"


def route_for(intent: str | None) -> str:
    return INTENT_ROUTES.get(intent, FALLBACK_ROUTE) if intent else FALLBACK_ROUTE


def after_intent(state: dict) -> str:
    if state.get("ticket_request"):
        return "business"
    if state.get("history_recall"):
        return "business"
    route = state["route"]
    if route == "aftersales":
        return "ensure_order" if state.get("order_scoped") else "expand_query"
    return route


def after_gate(state: dict) -> str:
    return "agent_model" if state["gate"]["passed"] else "fallback_reply"


def after_agent(state: dict) -> str:
    last = state["agent_messages"][-1]
    calls = getattr(last, "tool_calls", None)
    if state.get("force_final") or not calls:
        return "finalize"
    if state.get("ticket_request") and any(c["name"] == TICKET_TOOL for c in calls):
        return "confirm_write"
    return "agent_tools"


def after_tools(state: dict) -> str:
    return "ticket_reply" if state.get("write_decision") is not None else "agent_model"
