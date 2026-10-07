import json
import asyncio
from dataclasses import replace
from datetime import date

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.runnables import RunnableLambda
from sqlalchemy import select

from app.config import RERANK_MIN_SCORE
from app.db.models import FaithCase
from app.knowledge.retrieval import EvidenceItem, Retrieval
from app.prompts import REFUSAL_PREFIX
from app.schemas import FaithVerdict, QueryPlan, SelfCheck
from app.services import grounding
from evals import rag_metrics as rm
from evals import run_rag_eval as rre
from evals import rag_eval_set as es
from evals import run_faith_judge_eval as fje
from evals.rag_eval_set import EvalSample
from tests.fakes import Recorder, ScriptedChatModel, text, tools

pytestmark = pytest.mark.anyio
PLAN = QueryPlan(standard_query="X3 Pro 续航", product_category="蓝牙耳机")
SAMPLE = EvalSample("B01", "B_model", "easy", "X3 Pro 能用多久", ("商品手册 > 蓝牙耳机 > X3 Pro 续航",))
ITEM = EvidenceItem(11, "商品手册 > 蓝牙耳机 > X3 Pro 续航", "X3 Pro 续航", "单次续航 8 小时", 0.9)


def fixed(result):
    calls = []

    def run(inputs):
        calls.append(inputs)
        return {"parsed": result, "raw": None}
    return RunnableLambda(run), calls


@pytest.fixture
def fake_retrieve(monkeypatch):
    seen = []

    async def run(question, strategy, **kw):
        seen.append((question, strategy, kw))
        return Retrieval(kw["plan"], [ITEM], [ITEM])

    monkeypatch.setattr(rre, "retrieve", run)
    return seen


async def test_generate_one_uses_model_tool_call_id(fake_retrieve):
    rec = Recorder()
    model = ScriptedChatModel(scripts=[tools(("call_x", "query_faq", {"question": "别的说法"})),
                                       text("约 8 小时[1]")], recorder=rec)
    checker, _ = fixed(SelfCheck(useful=True, reason="[1]"))
    judge, judge_calls = fixed(FaithVerdict(faithful=True, unsupported_claims=[], reason="ok"))
    res = await rre.generate_one(SAMPLE, PLAN, "hybrid_rerank", model=model, judge=judge,
                                 checker=checker, today=date(2026, 10, 7))
    assert fake_retrieve == [("X3 Pro 能用多久", "hybrid_rerank", {"plan": PLAN, "exclude_mined": True})]
    msgs = rec[1]["messages"]
    ai = next(m for m in msgs if isinstance(m, AIMessage) and m.tool_calls)
    tool = next(m for m in msgs if isinstance(m, ToolMessage))
    assert ai.tool_calls[0]["id"] == "call_x" and tool.tool_call_id == "call_x"
    assert json.loads(tool.content)["data"]["evidence"][0]["n"] == 1
    assert (res.retrieved, res.refused, res.faithful, res.answer) == (True, False, True, "约 8 小时[1]")
    assert res.citations == [{"n": 1, "chunk_id": 11, "section_path": ITEM.section_path,
                              "question": ITEM.question, "answer": ITEM.answer}]
    assert judge_calls[0]["answer"] == "约 8 小时[1]" and "[1] 商品手册" in judge_calls[0]["evidence"]


async def test_generate_one_without_faq_call(fake_retrieve):
    model = ScriptedChatModel(scripts=[text("您好")])
    judge, judge_calls = fixed(None)
    res = await rre.generate_one(SAMPLE, PLAN, "bm25", model=model, judge=judge,
                                 checker=fixed(None)[0], today=date(2026, 10, 7))
    assert (res.retrieved, res.answer) == (False, "您好") and fake_retrieve == [] and judge_calls == []


async def test_generate_one_refused_skips_judge(fake_retrieve):
    rec = Recorder()
    model = ScriptedChatModel(scripts=[tools(("c1", "query_faq", {"question": "q"})),
                                       text(REFUSAL_PREFIX + "建议转人工")], recorder=rec)
    judge, judge_calls = fixed(None)
    res = await rre.generate_one(SAMPLE, PLAN, "hybrid_rerank", model=model, judge=judge,
                                 checker=fixed(SelfCheck(useful=False, reason="缺"))[0], today=date(2026, 10, 7))
    tool = next(m for m in rec[1]["messages"] if isinstance(m, ToolMessage))
    assert tool.content == grounding.REFUSED_CONTENT
    assert res.refused is True and res.citations == [] and judge_calls == []


async def test_other_tool_calls_are_not_executed(fake_retrieve):
    rec = Recorder()
    model = ScriptedChatModel(scripts=[
        tools(("c1", "query_order", {"order_id": "1001"}), ("c2", "query_faq", {"question": "q"}),
              ("c3", "create_ticket", {"title": "t", "description": "d"})),
        text("答[1]"),
    ], recorder=rec)
    judge, _ = fixed(FaithVerdict(faithful=True, unsupported_claims=[], reason="ok"))
    await rre.generate_one(SAMPLE, PLAN, "hybrid_rerank", model=model, judge=judge,
                           checker=fixed(SelfCheck(useful=True, reason="ok"))[0], today=date(2026, 10, 7))
    contents = {m.tool_call_id: m.content for m in rec[1]["messages"] if isinstance(m, ToolMessage)}
    order = json.loads(contents["c1"])
    assert order["ok"] is True and "1001" in contents["c1"]
    assert json.loads(contents["c3"]) == {"ok": False, "error": "tool_error", "message": "查询失败"}


async def test_non_faq_tool_call_still_makes_second_call(fake_retrieve):
    model = ScriptedChatModel(scripts=[tools(("c1", "query_product", {"product_id": "P1001"})),
                                       text("L5 的参数请见商品页")])
    judge, judge_calls = fixed(None)
    res = await rre.generate_one(SAMPLE, PLAN, "hybrid_rerank", model=model, judge=judge,
                                 checker=fixed(None)[0], today=date(2026, 10, 7))
    assert (res.retrieved, res.answer, res.citations) == (False, "L5 的参数请见商品页", [])
    assert fake_retrieve == [] and judge_calls == []


async def test_write_faith_cases_filters(db):
    def r(bucket, strategy="hybrid_rerank", faithful=False, refused=False, retrieved=True, sid="A01"):
        return rm.GenResult(sid, bucket, "easy", strategy, "q", retrieved, refused, "答[1]",
                            [{"n": 1, "chunk_id": 1, "section_path": "p", "question": "q", "answer": "a"}],
                            faithful, ["编的句子"], "编了")
    n = await rre.write_faith_cases([
        r("A_policy", sid="A01"),
        r("A_policy", strategy="bm25", sid="A02"),
        r("D_unanswerable", sid="D01"),
        r("B_model", faithful=True, sid="B01"),
        r("C_colloquial", faithful=None, sid="C01"),
        r("E_multi", refused=True, sid="E01"),
    ], "m1")
    assert n == 1
    async with db() as s:
        [row] = list(await s.scalars(select(FaithCase)))
    assert (row.eval_id, row.bucket, row.judge_model) == ("A01", "A_policy", "m1")
    assert "编了" in row.reason and "编的句子" in row.reason


@pytest.mark.parametrize("judge_raises", [False, True])
async def test_generate_one_judge_failure_is_counted(fake_retrieve, judge_raises):
    def verdict(inputs):
        if judge_raises:
            raise RuntimeError("上游错误")
        return {"parsed": None}

    model = ScriptedChatModel(scripts=[tools(("c1", "query_faq", {"question": "q"})), text("答[1]")])
    result = await rre.generate_one(
        SAMPLE, PLAN, "hybrid_rerank", model=model, judge=RunnableLambda(verdict),
        checker=fixed(SelfCheck(useful=True, reason="ok"))[0], today=date(2026, 10, 7),
    )
    assert result.faithful is None and result.reason == "裁判失败"
    assert rm.generation_summary([result])["hybrid_rerank"]["judge_failed"] == 1


async def test_generate_one_two_faq_calls_hide_rejected_evidence(fake_retrieve):
    rec = Recorder()
    model = ScriptedChatModel(scripts=[
        tools(("c1", "query_faq", {"question": "q1"}), ("c2", "query_faq", {"question": "q2"})),
        text("编了 10 小时"),
    ], recorder=rec)
    judge, calls = fixed(FaithVerdict(faithful=False, unsupported_claims=["编了 10 小时"], reason="缺证据"))
    result = await rre.generate_one(
        SAMPLE, PLAN, "hybrid_rerank", model=model, judge=judge,
        checker=fixed(SelfCheck(useful=False, reason="缺"))[0], today=date(2026, 10, 7),
    )
    assert len(fake_retrieve) == 1 and result.citations == []
    assert calls == [{"evidence": "（无证据）", "answer": "编了 10 小时"}]
    messages = rec[1]["messages"]
    assert [m.tool_call_id for m in messages if isinstance(m, ToolMessage)] == ["c1", "c2"]
    assert all(m.content == grounding.REFUSED_CONTENT for m in messages if isinstance(m, ToolMessage))
    assert messages[-1].content == rre.TOOL_ROUND_CLOSING


@pytest.fixture
def offline_eval(monkeypatch, tmp_path):
    samples = []
    for bucket, sizes in es.DIFFICULTY_SIZES.items():
        index = 0
        for difficulty, size in zip(rm.DIFFICULTIES, sizes):
            for _ in range(size):
                index += 1
                sid = f"{bucket[0]}{index:02d}"
                relevant = () if bucket == "D_unanswerable" else ((ITEM.section_path,),)
                if bucket == "E_multi":
                    relevant += (("其他来源",),)
                samples.append(EvalSample(sid, bucket, difficulty, f"{sid} q", relevant))
    lifecycle = []

    def event(name):
        async def run():
            lifecycle.append(name)
        return run

    async def keys():
        return {ITEM.section_path: "预览", "其他来源": "预览"}

    monkeypatch.setattr(rre, "ensure_collection", event("ensure"))
    monkeypatch.setattr(rre, "close_milvus", event("milvus"))
    monkeypatch.setattr(rre, "close_rerank", event("rerank"))
    monkeypatch.setattr(rre, "dispose_engine", event("db"))
    monkeypatch.setattr(rre, "known_source_keys", keys)
    monkeypatch.setattr(rre, "load_samples", lambda: samples)
    monkeypatch.setattr(rre, "REPORTS_DIR", tmp_path)
    return samples, lifecycle, tmp_path


async def test_retrieval_stage_caches_plans_and_uses_ranked_before_threshold(offline_eval, monkeypatch):
    _, lifecycle, report_dir = offline_eval
    understood, seen = [], []
    active = peak = 0

    async def understand(query):
        understood.append(query)
        return PLAN

    async def retrieve(query, strategy, **kwargs):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0)
        active -= 1
        seen.append((query, strategy, kwargs))
        return Retrieval(PLAN, [replace(ITEM, score=RERANK_MIN_SCORE - 0.1)], [])

    monkeypatch.setattr(rre, "understand", understand)
    monkeypatch.setattr(rre, "retrieve", retrieve)
    args = rre.build_parser().parse_args(["--stage", "retrieval", "--bucket", "B_model", "--limit", "2",
                                         "--concurrency", "2"])
    assert await rre.run_eval(args) == 0
    assert understood == ["B01 q", "B02 q"] and len(seen) == 8
    assert {s for _, s, _ in seen} == set(rre.STRATEGIES) and peak == 2
    assert all(kw == {"plan": PLAN, "exclude_mined": True} for _, _, kw in seen)
    [report] = report_dir.glob("*.md")
    md = report.read_text()
    before, after = md.split("## hybrid_rerank 门槛后")
    assert "| hybrid_rerank | B_model | 1.000 |" in before
    assert "| hybrid_rerank | B_model | 0.000 |" in after
    assert "评估集题数：300；本次题数：2" in md and "## 生成" not in md
    assert lifecycle == ["ensure", "milvus", "rerank", "db"]


async def test_retrieval_failures_continue_and_report(offline_eval, monkeypatch):
    _, lifecycle, report_dir = offline_eval
    seen = []

    async def understand(query):
        if query == "B01 q":
            raise ValueError("改写失败")
        return PLAN

    async def retrieve(query, strategy, **kwargs):
        seen.append((query, strategy))
        if strategy == "bm25":
            raise RuntimeError("检索失败")
        return Retrieval(PLAN, [ITEM], [ITEM])

    monkeypatch.setattr(rre, "understand", understand)
    monkeypatch.setattr(rre, "retrieve", retrieve)
    args = rre.build_parser().parse_args(["--stage", "retrieval", "--bucket", "B_model", "--limit", "2"])
    assert await rre.run_eval(args) == 1 and len(seen) == 4
    md = next(report_dir.glob("*.md")).read_text()
    assert "B01/understand：ValueError" in md and "B02/bm25：RuntimeError" in md
    assert "## 执行失败" in md and lifecycle[-3:] == ["milvus", "rerank", "db"]


@pytest.mark.parametrize("flag", ["--check", "--list-keys"])
async def test_startup_modes_do_not_invoke_models(offline_eval, flag, capsys):
    _, lifecycle, report_dir = offline_eval
    assert await rre.run_eval(rre.build_parser().parse_args([flag])) == 0
    output = capsys.readouterr().out
    assert ("评估集检查通过" if flag == "--check" else ITEM.section_path + "\t预览") in output
    assert not list(report_dir.glob("*.md"))
    assert lifecycle == ["ensure", "milvus", "rerank", "db"]


async def test_startup_validates_full_set_before_filter(offline_eval, monkeypatch, capsys):
    samples, lifecycle, report_dir = offline_eval
    monkeypatch.setattr(rre, "load_samples", lambda: samples[:1])
    args = rre.build_parser().parse_args(["--bucket", "A_policy", "--limit", "1"])
    assert await rre.run_eval(args) == 1
    assert "A_policy：题数 1，应为 70" in capsys.readouterr().out
    assert lifecycle[-3:] == ["milvus", "rerank", "db"] and not list(report_dir.glob("*.md"))


@pytest.mark.parametrize("no_write", [False, True])
async def test_generation_stage_all_strategies_and_judge_failure_exit(offline_eval, monkeypatch, no_write):
    _, _, report_dir = offline_eval
    generated, written = [], []
    model, judge = object(), object()

    async def understand(query):
        return PLAN

    async def generate(sample, plan, strategy, **kwargs):
        assert plan is PLAN and kwargs["model"] is model and kwargs["judge"] is judge
        generated.append(strategy)
        return rm.GenResult(sample.id, sample.bucket, sample.difficulty, strategy, sample.query,
                            True, False, "答", [], None if strategy == "bm25" else False, ["答"], "缺")

    async def write(results, judge_model):
        written.extend(results)
        assert judge_model == "m1"
        return 1

    from types import SimpleNamespace
    monkeypatch.setattr(rre, "understand", understand)
    monkeypatch.setattr(rre, "get_chat_model", lambda: model)
    monkeypatch.setattr(rre, "get_faith_judge", lambda: judge)
    monkeypatch.setattr(rre, "get_settings", lambda: SimpleNamespace(chat_model="m1"))
    monkeypatch.setattr(rre, "generate_one", generate)
    monkeypatch.setattr(rre, "write_faith_cases", write)
    options = ["--stage", "generation", "--bucket", "B_model", "--limit", "1", "--gen-strategies", "all"]
    if no_write:
        options.append("--no-write")
    assert await rre.run_eval(rre.build_parser().parse_args(options)) == 1
    assert generated == list(rre.STRATEGIES) and len(written) == (0 if no_write else 4)
    md = next(report_dir.glob("*.md")).read_text()
    assert "## 检索" not in md and "B01/bm25：裁判失败" in md and "未找到依据：答" in md


@pytest.mark.parametrize("fail_one, mismatches, expected", [(False, 0, 0), (False, 2, 1), (True, 0, 1)])
async def test_faith_judge_accuracy_and_any_call_failure(monkeypatch, tmp_path, capsys, fail_one, mismatches, expected):
    # 一次失败时准确率仍为 90%，但必须返回失败退出码。
    samples = [{"id": f"F{i:02d}", "evidence": [{"n": 1, "section_path": "p", "question": "q", "answer": "a"}],
                "answer": str(i), "faithful": True} for i in range(10)]
    path = tmp_path / "samples.jsonl"
    path.write_text("\n".join(json.dumps(s) for s in samples))
    calls = []

    def verdict(inputs):
        calls.append(inputs)
        index = int(inputs["answer"])
        if fail_one and index == 0:
            raise RuntimeError("调用失败")
        return {"parsed": FaithVerdict(faithful=index >= mismatches, reason="ok")}

    monkeypatch.setattr(fje, "SAMPLES_PATH", path)
    monkeypatch.setattr(fje, "get_faith_judge", lambda: RunnableLambda(verdict))
    assert await fje.run_eval() == expected and len(calls) == 10
    assert all(c["evidence"] == "[1] p\n问：q\n答：a" for c in calls)
    assert "忠实度裁判准确率" in capsys.readouterr().out
