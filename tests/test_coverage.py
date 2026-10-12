import pytest
from langchain_core.runnables import RunnableLambda

from app.db.models import KnowledgeChunk
from app.prompts import coverage_judge_prompt
from app.schemas import CoverageVerdict, GroupCoverage
from evals import coverage as cv
from evals.rag_eval_set import EvalSample

E = EvalSample("E01", "E_multi", "hard", "X5 Pro 续航和保修？", (("k1", "k1b"), ("k2",)))
TEXTS = {"k1": "续航 12 小时", "k2": "保修 365 天"}


def verdict(*items):
    return CoverageVerdict(groups=[GroupCoverage(n=n, covered=c, reason=f"r{n}") for n, c in items])


def judge(result=None, exc=None):
    calls = []

    def run(inputs):
        calls.append(inputs)
        if exc:
            raise exc
        return {"parsed": result, "raw": None}
    return RunnableLambda(run), calls


def test_prompt_renders_three_parts():
    msgs = coverage_judge_prompt.format_messages(question="Q", references="[1] k\nT", answer="A")
    assert msgs[-1].content == "问题：Q\n\n参考事实：\n[1] k\nT\n\n答案：\nA"


def test_format_references_numbers_from_one():
    assert cv.format_references(["k1", "k2"], TEXTS) == "[1] k1\n续航 12 小时\n\n[2] k2\n保修 365 天"


def test_parse_verdict_reorders_by_number():
    assert cv.parse_verdict(verdict((2, False), (1, True)), 2) == ([True, False], ["r1", "r2"])


@pytest.mark.parametrize("items", [[(1, True)], [(1, True), (1, False)], [(1, True), (3, False)],
                                   [(0, True), (1, False)]])
def test_parse_verdict_rejects_bad_numbers(items):
    with pytest.raises(ValueError):
        cv.parse_verdict(verdict(*items), 2)


@pytest.mark.anyio
async def test_judge_coverage_calls_judge_with_first_keys():
    runnable, calls = judge(verdict((1, True), (2, False)))
    r = await cv.judge_coverage(E, "hybrid_rerank", "答", False, TEXTS, runnable)
    assert r.covered == [True, False] and r.reasons == ["r1", "r2"] and r.error is None
    assert r.keys == ["k1", "k2"] and r.strategy == "hybrid_rerank" and r.bucket == "E_multi"
    assert calls == [{"question": E.query, "references": cv.format_references(["k1", "k2"], TEXTS),
                      "answer": "答"}]


@pytest.mark.anyio
async def test_judge_coverage_refused_skips_judge():
    runnable, calls = judge(verdict((1, True), (2, True)))
    r = await cv.judge_coverage(E, "hybrid_rerank", "抱歉", True, TEXTS, runnable)
    assert r.covered == [False, False] and r.reasons == ["拒答", "拒答"] and not calls and r.refused


@pytest.mark.anyio
async def test_judge_coverage_missing_reference_is_error():
    runnable, calls = judge(verdict((1, True), (2, True)))
    r = await cv.judge_coverage(E, "hybrid_rerank", "答", False, {"k1": "x"}, runnable)
    assert r.covered is None and r.error == "参考原文缺失：k2" and not calls


@pytest.mark.anyio
@pytest.mark.parametrize("result, exc", [(None, None), (None, RuntimeError("x")),
                                         (verdict((1, True)), None)])
async def test_judge_coverage_failures(result, exc):
    runnable, _ = judge(result, exc)
    r = await cv.judge_coverage(E, "hybrid_rerank", "答", False, TEXTS, runnable)
    assert r.covered is None and r.error == "覆盖率裁判失败"


def result(sid, bucket, covered, refused=False):
    return cv.CoverageResult(sid, bucket, "hybrid_rerank", ["k"] * (len(covered) if covered else 1),
                             refused, covered, ["r"] * len(covered or []))


def test_coverage_summary_two_scopes():
    rows = [result("E1", "E_multi", [True, False]), result("E2", "E_multi", [True, True]),
            result("E3", "E_multi", [False, False], refused=True), result("E4", "E_multi", None),
            result("B1", "B_model", [True])]
    s = cv.coverage_summary(rows)
    e = s[("hybrid_rerank", "E_multi")]
    assert (e["n"], e["refused"], e["failed"]) == (4, 1, 1)
    assert e["group_all"] == pytest.approx(3 / 6) and e["full_all"] == pytest.approx(1 / 3)
    assert e["group_answered"] == pytest.approx(3 / 4) and e["full_answered"] == pytest.approx(1 / 2)
    a = s[("hybrid_rerank", "ALL")]
    assert a["n"] == 5 and a["group_all"] == pytest.approx(4 / 7)
    assert list(s) == [("hybrid_rerank", "B_model"), ("hybrid_rerank", "E_multi"), ("hybrid_rerank", "ALL")]


def test_coverage_summary_empty_denominator_is_none():
    s = cv.coverage_summary([result("E1", "E_multi", [False], refused=True)])
    assert s[("hybrid_rerank", "E_multi")]["group_answered"] is None


def test_render_coverage_table_and_cases():
    rows = [cv.CoverageResult("E1", "E_multi", "hybrid_rerank", ["k1", "k2"], False, [True, False],
                              ["ok", "漏了|保修"]),
            cv.CoverageResult("E2", "E_multi", "hybrid_rerank", ["k1"], True, [False], ["拒答"]),
            cv.CoverageResult("E3", "E_multi", "hybrid_rerank", ["k1"], False, None, [], "覆盖率裁判失败")]
    md = cv.render_coverage(rows)
    assert md.startswith("## 事实覆盖率") and "## 未覆盖个案" in md
    assert "- E1 [hybrid_rerank] 组 2 k2 → 漏了\\|保修" in md and "- E2 [hybrid_rerank] 拒答" in md
    assert "E3 [hybrid_rerank]" not in md
    assert "| hybrid_rerank | E_multi | 3 | 1 | 1 | 0.333 | 0.000 | 0.500 | 0.000 |" in md


@pytest.mark.anyio
async def test_load_reference_texts_joins_and_filters(db):
    async with db() as s:
        for path, q, kind, status, ans in [
            ("退货政策 > 品类退换货规则", "品类退换货规则", "policy", "done", "第一块"),
            ("退货政策 > 品类退换货规则", "品类退换货规则", "policy", "done", "第二块"),
            ("常见问答 > 运费", "怎么算", "faq", "done", "包邮"),
            ("挖掘问答 > 运费", "怎么算", "mined", "done", "挖掘"),
            ("飞轮 > 运费", "怎么算", "flywheel", "done", "飞轮"),
            ("商品手册 > X5", "续航", "manual", "pending", "未向量化"),
            ("商品手册 > X3", "续航", "manual", "done", "不需要"),
        ]:
            s.add(KnowledgeChunk(category="c", section_path=path, questions=q, answer=ans,
                                 content_type=kind, vectorize_status=status))
        await s.commit()
    got = await cv.load_reference_texts(["退货政策 > 品类退换货规则", "常见问答 > 运费 > 怎么算",
                                         "挖掘问答 > 运费", "飞轮 > 运费", "商品手册 > X5"])
    assert got == {"退货政策 > 品类退换货规则": "第一块\n第二块", "常见问答 > 运费 > 怎么算": "包邮"}
