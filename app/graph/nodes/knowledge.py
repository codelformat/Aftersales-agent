"""知识类出口：强制检索，再过置信度闸。"""

from dataclasses import asdict

from app.config import GATE_MIN_SCORE, get_settings
from app.graph import events
from app.knowledge.retrieval import Retrieval, retrieve
from app.schemas import QueryPlan
from app.services.grounding import (
    EMPTY_EVIDENCE_REASON,
    Citation,
    collect_evidence,
    record_low_confidence,
    self_check,
)


def evidence_update(question: str, result: Retrieval, trace: list[str]) -> dict:
    evidence = collect_evidence([("retrieve", question, {"evidence": [asdict(e) for e in result.evidence]})])
    top = result.ranked[0].score if result.ranked else None
    return {
        "evidence": [c.to_dict() for c in evidence.citations],
        "gate": {"passed": False, "top_score": top, "reason": "", "source": None},
        "trace": trace,
    }


async def retrieve_evidence(state, runtime):
    trace = events.enter("retrieve", state, runtime)
    plan = QueryPlan(standard_query=state["standard_query"], product_category=state.get("product_category"))
    result = await retrieve(state["resolved_input"], plan=plan, top_n=get_settings().rerank_top_k)
    return evidence_update(state["resolved_input"], result, trace)


async def confidence_gate(state, runtime):
    trace = events.enter("confidence_gate", state, runtime)
    citations = [Citation(**c) for c in state["evidence"]]
    top = state["gate"]["top_score"]
    if not citations or top is None or top < GATE_MIN_SCORE:
        gate = {"passed": False, "top_score": top, "reason": EMPTY_EVIDENCE_REASON,
                "source": "retrieval_low_conf"}
    elif state.get("route") == "aftersales":
        # 子流程中缺的信息由 Agent 追问，自评会挡在 Agent 之前。
        gate = {"passed": True, "top_score": top, "reason": "", "source": None}
    else:
        check = await self_check([state["resolved_input"]], citations)
        gate = {"passed": check.useful, "top_score": top, "reason": check.reason,
                "source": None if check.useful else "self_check"}
    if gate["passed"]:
        events.emit("citations", {"items": state["evidence"], "refused": False})
    else:
        # 独立事务，失败只记日志。
        await record_low_confidence(
            runtime.context.conversation_id, state["user_input"], gate["reason"], source=gate["source"])
    return {"gate": gate, "trace": trace}
