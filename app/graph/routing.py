"""分流规则写死在代码中。"""

INTENT_ROUTES = {
    "商品咨询": "knowledge",
    "退款退货": "knowledge",
    "物流": "business",
    "订单": "business",
    "售后": "business",
    "投诉": "complaint",
    "闲聊": "chitchat",
    "其他": "business",
}
# 意图识别失败时走 business：Agent 有工具，能处理大多数问题。
FALLBACK_ROUTE = "business"


def route_for(intent: str | None) -> str:
    return INTENT_ROUTES.get(intent, FALLBACK_ROUTE) if intent else FALLBACK_ROUTE


def after_intent(state: dict) -> str:
    return state["route"]


def after_gate(state: dict) -> str:
    return "agent_model" if state["gate"]["passed"] else "fallback_reply"


def after_agent(state: dict) -> str:
    last = state["agent_messages"][-1]
    if getattr(last, "tool_calls", None) and not state.get("force_final"):
        return "agent_tools"
    return "finalize"
