import asyncio
from time import perf_counter

import pytest
from langchain_core.runnables import RunnableLambda

import app.knowledge.query as q
import app.config as config
from app.schemas import QueryPlan

pytestmark = pytest.mark.anyio

LEX = q.Lexicon(
    model_categories={"X3": "蓝牙耳机", "X3 Pro": "蓝牙耳机", "X5": "蓝牙耳机",
                      "S10": "扫地机器人", "S10 Max": "扫地机器人"},
    synonyms={"运费": ("邮费", "快递费"), "无理由退货": ("七天无理由", "不想要了能退吗")},
)


@pytest.mark.parametrize("raw, expected", [
    ("x3pro续航多久", "X3 Pro续航多久"),
    ("X3-PRO 怎么样", "X3 Pro 怎么样"),
    ("x3 pro和x5哪个好", "X3 Pro和X5哪个好"),
    ("s10max尘盒多大", "S10 Max尘盒多大"),
    ("X3 Pro 续航", "X3 Pro 续航"),
    ("X30 是什么", "X30 是什么"),
    ("AX3 是什么", "AX3 是什么"),
    ("退款多久到账", "退款多久到账"),
])
def test_normalize_models_variants(raw, expected):
    assert q.normalize_models(raw, LEX) == expected


def test_model_category():
    assert q.model_category("X3 Pro续航", LEX) == "蓝牙耳机"
    assert q.model_category("S10 Max 尘盒", LEX) == "扫地机器人"
    assert q.model_category("退款多久", LEX) is None


def test_dense_query_replaces_aliases_once():
    assert q.dense_query("邮费多少钱", LEX) == "运费多少钱"
    assert q.dense_query("七天无理由吗", LEX) == "无理由退货吗"
    assert q.dense_query("运费多少钱", LEX) == "运费多少钱"


def test_bm25_query_appends_synonyms():
    assert q.bm25_query("邮费多少钱", LEX) == "邮费多少钱 运费 快递费"
    assert q.bm25_query("运费怎么算", LEX) == "运费怎么算 邮费 快递费"
    assert q.bm25_query("X3 Pro 续航", LEX) == "X3 Pro 续航"


def _rewriter(plan=None, exc=None, seen=None):
    def run(inputs):
        if seen is not None:
            seen.append(inputs["question"])
        if exc:
            raise exc
        return {"parsed": plan, "raw": "raw"}
    return RunnableLambda(run)


async def test_understand_normalizes_before_and_after_llm():
    seen = []
    plan = await q.understand(
        "x3pro能用几个小时啊气死了",
        rewriter=_rewriter(QueryPlan(standard_query="x3pro 续航时间", product_category=None), seen=seen),
        lexicon=LEX,
    )
    assert seen == ["X3 Pro能用几个小时啊气死了"]
    assert plan == QueryPlan(standard_query="X3 Pro 续航时间", product_category="蓝牙耳机")


async def test_model_category_overrides_llm_category():
    plan = await q.understand(
        "X3 续航", rewriter=_rewriter(QueryPlan(standard_query="X3 续航", product_category="羊毛衫")),
        lexicon=LEX,
    )
    assert plan.product_category == "蓝牙耳机"


async def test_llm_category_kept_without_model():
    plan = await q.understand(
        "耳机能退吗", rewriter=_rewriter(QueryPlan(standard_query="蓝牙耳机无理由退货", product_category="蓝牙耳机")),
        lexicon=LEX,
    )
    assert plan.product_category == "蓝牙耳机"


@pytest.mark.parametrize("rewriter", [
    _rewriter(exc=TimeoutError("slow")),
    _rewriter(plan=None),
])
async def test_understand_falls_back_on_failure(rewriter, caplog):
    plan = await q.understand("x3pro 续航", rewriter=rewriter, lexicon=LEX)
    assert plan == QueryPlan(standard_query="X3 Pro 续航", product_category="蓝牙耳机")
    assert "Query 改写失败" in caplog.text


async def test_understand_slow_rewriter_falls_back_within_budget(monkeypatch, caplog):
    monkeypatch.setattr(config, "QUERY_REWRITE_TIMEOUT_SECONDS", 0.05, raising=False)

    async def slow_rewriter(inputs):
        await asyncio.sleep(1)
        return {"parsed": QueryPlan(standard_query="改写后的问题", product_category=None), "raw": "raw"}

    started = perf_counter()
    plan = await q.understand("x3pro 续航", rewriter=RunnableLambda(slow_rewriter), lexicon=LEX)
    elapsed = perf_counter() - started

    assert elapsed < 0.5
    assert plan == QueryPlan(standard_query="X3 Pro 续航", product_category="蓝牙耳机")
    assert "Query 改写失败" in caplog.text


async def test_understand_without_rewriter_uses_factory_and_is_blocked_in_tests():
    with pytest.raises(RuntimeError, match="get_query_rewriter"):
        await q.understand("运费", lexicon=LEX)


def test_real_lexicon_loads():
    lex = q.load_lexicon()
    assert len(lex.model_categories) == 24
    assert lex.model_categories["X3 Pro"] == "蓝牙耳机"
    assert "邮费" in lex.synonyms["运费"]


@pytest.mark.parametrize("raw, expected", [
    ("七天无理由退货怎么办", "无理由退货怎么办"),
    ("怎么恢复出厂设置", "怎么恢复出厂设置"),
    ("七天无理由吗", "无理由退货吗"),
    ("邮费多少钱", "运费多少钱"),
])
def test_dense_query_real_lexicon_avoids_duplicate_standard_suffix(raw, expected):
    result = q.dense_query(raw, q.load_lexicon())
    assert result == expected
    assert "退货退货" not in result


def test_query_plan_rejects_unknown_category():
    with pytest.raises(ValueError):
        QueryPlan(standard_query="x", product_category="冰箱")


@pytest.mark.parametrize("raw", ["null", " ", "None", " NULL ", ""])
def test_query_plan_null_like_category_is_none(raw):
    assert QueryPlan(standard_query="x", product_category=raw).product_category is None
