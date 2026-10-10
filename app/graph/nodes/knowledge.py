"""知识类出口：强制检索，再过置信度闸。"""

from dataclasses import asdict

from app.config import GATE_CONF_THRESHOLD, GATE_EFFECTIVE_N, GATE_WEIGHTS, SNAPSHOT_TOP_N, get_settings
from app.graph import events
from app.knowledge.retrieval import EvidenceItem, Retrieval, retrieve
from app.schemas import QueryPlan
from app.services.confidence import gate_passes
from app.services.grounding import (
    EMPTY_EVIDENCE_REASON,
    Citation,
    collect_evidence,
    record_low_confidence,
    self_check,
)


def snapshot(ranked: list[EvidenceItem]) -> list[dict]:
    """召回快照：保存重排后、门槛过滤前的前几条，供审核人员判断。"""
    return [{"chunk_id": e.chunk_id, "section_path": e.section_path, "question": e.question,
             "answer": e.answer, "score": round(e.score, 4)} for e in ranked[:SNAPSHOT_TOP_N]]


def evidence_update(question: str, result: Retrieval, trace: list[str]) -> dict:
    evidence = collect_evidence([("retrieve", question, {"evidence": [asdict(e) for e in result.evidence]})])
    top = result.ranked[0].score if result.ranked else None
    return {
        "evidence": [c.to_dict() for c in evidence.citations],
        "retrieval": snapshot(result.ranked),
        "gate": {"passed": False, "top_score": top, "reason": "", "source": None},
        "trace": trace,
    }


async def retrieve_evidence(state, runtime):
    trace = events.enter("retrieve", state, runtime)
    plan = QueryPlan(standard_query=state["standard_query"], product_category=state.get("product_category"))
    result = await retrieve(state["resolved_input"], plan=plan, top_n=get_settings().rerank_top_k)
    update = evidence_update(state["resolved_input"], result, trace)
    events.trace("retrieval", {"queries": [state["standard_query"]], "top": update["retrieval"],
                               "kept": len(update["evidence"])}, node="retrieve")
    return update


async def confidence_gate(state, runtime):
    trace = events.enter("confidence_gate", state, runtime)
    citations = [Citation(**c) for c in state["evidence"]]
    snap = state.get("retrieval") or []
    ok, conf = gate_passes([c["score"] for c in snap], weights=GATE_WEIGHTS, effective_n=GATE_EFFECTIVE_N,
                           threshold=GATE_CONF_THRESHOLD)
    base = {"top_score": state["gate"]["top_score"], "confidence": round(conf.score, 4),
            "signals": {k: v for k, v in conf.to_dict().items() if k != "score"}}
    checked = False
    if not citations or not ok:
        gate = {**base, "passed": False, "reason": EMPTY_EVIDENCE_REASON, "source": "retrieval_low_conf"}
    elif state.get("route") == "aftersales":
        # 子流程中缺的信息由 Agent 追问，自评会挡在 Agent 之前。
        gate = {**base, "passed": True, "reason": "", "source": None}
    else:
        checked = True
        check = await self_check([state["resolved_input"]], citations)
        gate = {**base, "passed": check.useful, "reason": check.reason,
                "source": None if check.useful else "self_check"}
    events.trace("gate", {
        **{key: gate[key] for key in ("passed", "confidence", "signals", "source", "reason")},
        "weights": list(GATE_WEIGHTS), "threshold": GATE_CONF_THRESHOLD, "self_check": checked,
    }, node="confidence_gate")
    if gate["passed"]:
        events.emit("citations", {"items": state["evidence"], "refused": False})
    else:
        # 独立事务，失败只记日志。
        await record_low_confidence(
            runtime.context.conversation_id, state["user_input"], gate["reason"], source=gate["source"],
            retrieved_chunks=snap)
    return {"gate": gate, "trace": trace}
