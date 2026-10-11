import pytest

from app.db.models import KnowledgeChunk
from app.knowledge.query import load_lexicon
from app.knowledge.retrieval import FAQ_TABLE_SOURCE, source_key
from evals.hard_set import corpus, seeds

LEX = load_lexicon()


def manual_rows():
    rows = []
    for category, models in (("蓝牙耳机", ("X3", "X3 Pro", "X5")), ("台灯", ("L1", "L1 Pro", "L2"))):
        for model in models:
            for aspect in ("参数", "续航", "保修", "退换规则", "常见故障"):
                rows.append((f"商品手册 > {category} > {model} {aspect}", f"{model} {aspect}",
                             f"{model} 的{aspect}说明，数值 {len(model)}。"))
    return rows


def general_rows():
    return [
        ("商品手册", "商品手册", "手册简介"),
        ("售后手册 > 维修 > 维修流程", "维修流程", "第一块"),
        ("售后手册 > 维修 > 维修流程", "维修流程", "第二块"),
        ("售后手册 > 维修 > 保修期", "保修期", "保修期从签收次日开始计算"),
        ("退货政策 > 退货条件 > 七天无理由退货", "七天无理由退货", "签收后 7 天内"),
        ("退货政策 > 退货条件 > 质量问题退货", "质量问题退货", "签收后 15 天内"),
        ("退货政策 > 品类退换货规则", "品类退换货规则", "表格一"),
        ("退货政策 > 品类退换货规则", "品类退换货规则", "表格二"),
        ("退货政策 > 特殊情况 > 价格保护", "价格保护", "签收后 7 天内降价可申请价格保护"),
        ("常见问答 > 退换货", "退货政策是什么？", "7 天无理由"),
        ("常见问答 > 售后维修", "商品坏了怎么保修？", "保修期内免费维修"),
        ("商品FAQ > 蓝牙耳机 > 耳机充满电能用多久？", "耳机充满电能用多久？", "约 6 小时"),
        ("商品FAQ > 台灯 > 台灯有几档亮度？", "台灯有几档亮度？", "5 档"),
    ]


def chunks():
    return corpus.build_chunks(manual_rows() + general_rows(), LEX)


def test_chunk_key_matches_production_rule():
    assert corpus.FAQ_TABLE_SOURCE == FAQ_TABLE_SOURCE
    for path, q in (("常见问答 > 运费", "运费怎么算？"), ("退货政策 > 退款 > 退款时间", "退款时间")):
        assert corpus.chunk_key(path, q) == source_key(path, q)


def test_build_chunks_merges_split_chunks_and_parses_models():
    by_key = {c.key: c for c in chunks()}
    assert by_key["售后手册 > 维修 > 维修流程"].text == "第一块\n第二块"
    assert by_key["退货政策 > 品类退换货规则"].text == "表格一\n表格二"
    assert len([c for c in chunks() if c.key == "售后手册 > 维修 > 维修流程"]) == 1
    pro = by_key["商品手册 > 蓝牙耳机 > X3 Pro 续航"]
    assert (pro.model, pro.aspect, pro.category, pro.source) == ("X3 Pro", "续航", "蓝牙耳机", "商品手册")
    faq = by_key["常见问答 > 退换货 > 退货政策是什么？"]
    assert (faq.title, faq.category, faq.model) == ("退货政策是什么？", "通用", None)
    assert by_key["商品FAQ > 台灯 > 台灯有几档亮度？"].category == "台灯"


def test_pools_are_deterministic_and_slot_ordered():
    a, b = seeds.build_pools(chunks()), seeds.build_pools(chunks())
    assert a == b and set(a) == set(seeds.BUCKETS)
    for pool in a.values():
        slots = [s.slot for s in pool]
        assert slots == sorted(slots)                     # 全部 slot 1 在 slot 2 之前
        per_target = {}
        for s in pool:
            per_target[s.target_key] = per_target.get(s.target_key, 0) + 1
        assert max(per_target.values()) <= 2
    assert seeds.build_pools(chunks(), seed=1) != a


def test_pool_contents_per_bucket():
    pools = seeds.build_pools(chunks())
    h1 = [s for s in pools["H1_sibling"] if s.target_key == "商品手册 > 蓝牙耳机 > X3 Pro 续航"][0]
    assert set(h1.distractor_keys) == {"商品手册 > 蓝牙耳机 > X3 续航", "商品手册 > 蓝牙耳机 > X5 续航"}
    h2_targets = {s.target_key for s in pools["H2_colloquial"]}
    assert "商品手册 > 蓝牙耳机 > X3 常见故障" in h2_targets
    assert "商品手册 > 蓝牙耳机 > X3 参数" not in h2_targets
    assert not h2_targets & {"商品手册", "退货政策 > 品类退换货规则"}
    h3_targets = {s.target_key for s in pools["H3_buried"]}
    assert "退货政策 > 特殊情况 > 价格保护" in h3_targets
    assert not any(k.startswith("商品手册") for k in h3_targets)
    h4 = {s.target_key: s for s in pools["H4_specific"]}
    assert set(h4["商品手册 > 蓝牙耳机 > X3 保修"].distractor_keys) == {
        "售后手册 > 维修 > 保修期", "常见问答 > 售后维修 > 商品坏了怎么保修？"}
    assert "退货政策 > 品类退换货规则" in h4["商品手册 > 台灯 > L2 退换规则"].distractor_keys
    assert h4["商品手册 > 蓝牙耳机 > X3 Pro 续航"].distractor_keys == (
        "商品FAQ > 蓝牙耳机 > 耳机充满电能用多久？",)
    assert "商品手册 > 蓝牙耳机 > X3 续航" not in h4          # 默认型号不出 FAQ 冲突题
    assert "商品手册 > 蓝牙耳机 > X5 常见故障" not in h4


def test_pool_round_robin_across_categories():
    pool = seeds.build_pools(chunks())["H1_sibling"]
    first_two = [s.target_key.split(" > ")[1] for s in pool[:2]]
    assert set(first_two) == {"蓝牙耳机", "台灯"}


@pytest.mark.parametrize(("target_key", "expected"), [
    ("商品手册 > 蓝牙耳机 > X3 保修", (
        "售后手册 > 维修 > 保修期", "常见问答 > 售后维修 > 商品坏了怎么保修？")),
    ("商品手册 > 台灯 > L2 退换规则", (
        "退货政策 > 退货条件 > 七天无理由退货", "退货政策 > 退货条件 > 质量问题退货",
        "退货政策 > 品类退换货规则", "常见问答 > 退换货 > 退货政策是什么？")),
    ("商品手册 > 蓝牙耳机 > X3 Pro 续航", ("商品FAQ > 蓝牙耳机 > 耳机充满电能用多久？",)),
])
def test_topic_keys_for_preserves_distractor_rules(target_key, expected):
    all_chunks = chunks()
    target = next(c for c in all_chunks if c.key == target_key)
    assert seeds.topic_keys_for(target, all_chunks) == expected


@pytest.mark.anyio
async def test_load_chunks_reads_doc_sources_only(db):
    async with db() as s:
        s.add_all([
            KnowledgeChunk(category="商品手册 > 蓝牙耳机", questions="X3 续航", answer="6 小时",
                           section_path="商品手册 > 蓝牙耳机 > X3 续航", content_type="manual",
                           vectorize_status="done"),
            KnowledgeChunk(category="对话挖掘 > 发票", questions="能开专票吗？", answer="可以",
                           section_path="对话挖掘 > 发票", content_type="mined", vectorize_status="done"),
            KnowledgeChunk(category="飞轮补充", questions="q", answer="a",
                           section_path="飞轮补充 > x", content_type="flywheel", vectorize_status="done"),
        ])
        await s.commit()
    got = await corpus.load_chunks(LEX)
    assert [c.key for c in got] == ["商品手册 > 蓝牙耳机 > X3 续航"]
