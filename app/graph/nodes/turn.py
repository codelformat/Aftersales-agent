from app.graph import events


async def start_turn(state, runtime):
    return {
        "resolved_input": "", "intent": None, "route": "", "evidence": [], "gate": None,
        "agent_messages": [], "steps": 0, "tokens_used": 0, "force_final": False,
        "reply": "", "actions": [], "trace": events.enter("start_turn", {}, runtime),
    }


async def resolve_reference(state, runtime):
    # 最简版：原样透传。正式的指代消解放在下一章。
    return {"resolved_input": state["user_input"],
            "trace": events.enter("resolve_reference", state, runtime)}
