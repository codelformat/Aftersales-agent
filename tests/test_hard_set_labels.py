# tests/test_hard_set_labels.py
from langchain_core.prompts import ChatPromptTemplate

from app.knowledge.query import load_lexicon
from evals.hard_set import labels, prompts
from evals.hard_set.corpus import Chunk
from evals.hard_set.seeds import BUCKETS

LEX = load_lexicon()


def c(key, category, model=None, source="商品手册", aspect=None):
    return Chunk(key, key, key.split(" > ")[-1], f"{key} 正文", category, source, model, aspect)


CHUNKS = [
    c("商品手册 > 蓝牙耳机 > X3 续航", "蓝牙耳机", "X3"),
    c("商品手册 > 蓝牙耳机 > X3 Pro 续航", "蓝牙耳机", "X3 Pro"),
    c("商品手册 > 台灯 > L1 参数", "台灯", "L1"),
    c("商品FAQ > 蓝牙耳机 > 耳机充满电能用多久？", "蓝牙耳机", None, "商品FAQ"),
    c("售后手册 > 维修 > 保修期", "通用", None, "售后手册"),
]
BY_KEY = {x.key: x for x in CHUNKS}


def test_model_info_normalizes_model_and_includes_category():
    info = labels.model_info("x3pro 能用多久", LEX)
    assert info == "问题中的型号：X3 Pro（品类：蓝牙耳机）"


def test_model_info_without_model():
    assert labels.model_info("耳机就左边响", LEX) == "问题中没有型号。"


def test_model_info_multiple_models_preserves_order_and_deduplicates():
    assert labels.model_info("w1plus 和 x3pro，W1 Plus 能退吗", LEX) == (
        "问题中的型号：W1 Plus（品类：羊毛衫）；X3 Pro（品类：蓝牙耳机）")


def test_candidates_follow_production_filter():
    with_model = labels.candidates_for("x3pro 能用多久", CHUNKS, LEX)
    assert [x.key for x in with_model] == [
        "商品手册 > 蓝牙耳机 > X3 续航", "商品手册 > 蓝牙耳机 > X3 Pro 续航",
        "商品FAQ > 蓝牙耳机 > 耳机充满电能用多久？", "售后手册 > 维修 > 保修期"]
    assert labels.candidates_for("耳机就左边响", CHUNKS, LEX) == CHUNKS


def test_render_and_answer_keys():
    text = labels.render_candidates(CHUNKS[:2])
    assert text.startswith("[1] 商品手册 > 蓝牙耳机 > X3 续航\n") and "[2] 商品手册 > 蓝牙耳机 > X3 Pro 续航" in text
    assert labels.answer_keys([2, 1, 2], CHUNKS[:2]) == [
        "商品手册 > 蓝牙耳机 > X3 Pro 续航", "商品手册 > 蓝牙耳机 > X3 续航"]
    assert labels.answer_keys([3], CHUNKS[:2]) is None
    assert labels.answer_keys([0], CHUNKS[:2]) is None
    assert labels.answer_keys([], CHUNKS[:2]) is None


def test_recall_keys_empty_means_no_recall():
    assert labels.recall_keys([], CHUNKS[:2]) == []
    assert labels.recall_keys([], []) == []


def test_recall_keys_rejects_invalid_numbers():
    for numbers in ([3], [0], [-1], [1, 3], [2, 0]):
        assert labels.recall_keys(numbers, CHUNKS[:2]) is None
    assert labels.recall_keys([1], []) is None


def test_recall_keys_maps_and_deduplicates_in_order():
    assert labels.recall_keys([2, 1, 2], CHUNKS[:2]) == [
        "商品手册 > 蓝牙耳机 > X3 Pro 续航", "商品手册 > 蓝牙耳机 > X3 续航"]


def test_check_labels_rules():
    pro, x3 = BY_KEY["商品手册 > 蓝牙耳机 > X3 Pro 续航"], BY_KEY["商品手册 > 蓝牙耳机 > X3 续航"]
    faq, general = "商品FAQ > 蓝牙耳机 > 耳机充满电能用多久？", "售后手册 > 维修 > 保修期"
    assert labels.check_labels("H1_sibling", pro, [x3.key], BY_KEY) == "目标段落不在答案中"
    assert labels.check_labels("H1_sibling", pro, [pro.key, x3.key], BY_KEY) == "同系列其他型号也能回答"
    assert labels.check_labels("H1_sibling", x3, [x3.key, faq], BY_KEY) is None
    assert labels.check_labels("H4_specific", pro, [pro.key, faq], BY_KEY) == "通用段落也能回答"
    assert labels.check_labels("H4_specific", pro, [pro.key], BY_KEY) is None
    assert labels.check_labels("H2_colloquial", pro, [pro.key, faq, general, x3.key], BY_KEY) == "来源键超过 3 个"
    assert labels.check_labels("H2_colloquial", pro, [pro.key, faq, general], BY_KEY) is None


def test_same_topic_keys_includes_siblings_then_return_policies():
    target = c("商品手册 > 蓝牙耳机 > X3 退换规则", "蓝牙耳机", "X3", aspect="退换规则")
    chunks = [
        c("退货政策 > 品类退换货规则", "通用", source="退货政策"),
        c("商品手册 > 蓝牙耳机 > X5 退换规则", "蓝牙耳机", "X5", aspect="退换规则"),
        target,
        c("商品手册 > 台灯 > L1 退换规则", "台灯", "L1", aspect="退换规则"),
        c("商品手册 > 蓝牙耳机 > X3 保修", "蓝牙耳机", "X3", aspect="保修"),
        c("商品手册 > 蓝牙耳机 > X3 Pro 退换规则", "蓝牙耳机", "X3 Pro", aspect="退换规则"),
        c("退货政策 > 退货条件 > 质量问题退货", "通用", source="退货政策"),
        c("退货政策 > 退货条件 > 七天无理由退货", "通用", source="退货政策"),
        c("常见问答 > 退换货 > 退货政策是什么？", "通用", source="常见问答"),
        c("商品FAQ > 蓝牙耳机 > 能退吗？", "蓝牙耳机", source="商品FAQ", aspect="退换规则"),
    ]
    assert labels.same_topic_keys(target, chunks) == [
        "商品手册 > 蓝牙耳机 > X5 退换规则",
        "商品手册 > 蓝牙耳机 > X3 Pro 退换规则",
        "退货政策 > 退货条件 > 七天无理由退货",
        "退货政策 > 退货条件 > 质量问题退货",
        "退货政策 > 品类退换货规则",
        "常见问答 > 退换货 > 退货政策是什么？",
    ]


def test_same_topic_keys_without_model_is_empty():
    assert labels.same_topic_keys(CHUNKS[3], CHUNKS) == []


def test_same_topic_keys_deduplicates_and_excludes_target():
    target = c("商品FAQ > 蓝牙耳机 > X3 续航", "蓝牙耳机", "X3", "商品FAQ", "续航")
    sibling = c("商品FAQ > 蓝牙耳机 > X5 续航", "蓝牙耳机", "X5", "商品FAQ", "续航")
    faq = c("商品FAQ > 蓝牙耳机 > 能用多久？", "蓝牙耳机", source="商品FAQ")
    assert labels.same_topic_keys(target, [target, faq, sibling, sibling]) == [sibling.key, faq.key]


def test_pair_prompt_renders_chunk_and_query_with_json():
    info = "问题中的型号：X3（品类：蓝牙耳机）"
    messages = prompts.PAIR_PROMPT.format_messages(
        chunk_title="保修期", chunk_text="保修一年", query="X3 保多久？", model_info=info)
    assert info in messages[0].content
    assert "X3、T3、L1、C5" in messages[0].content
    assert "按品类列出的规则表，对该品类的所有型号成立。" in messages[0].content
    assert "段落标题：保修期" in messages[0].content
    assert "段落正文：保修一年" in messages[0].content
    assert 'JSON' in messages[0].content
    assert '{"answerable": true}' in messages[0].content
    assert '{"answerable": false}' in messages[0].content
    assert messages[-1].content == "顾客问题：X3 保多久？"


def test_recall_prompt_renders_general_candidates_and_model_info_with_json():
    info = "问题中的型号：X3（品类：蓝牙耳机）"
    messages = prompts.RECALL_PROMPT.format_messages(
        candidates="[1] 申请换货\n只能换同款", query="X3 换货只能换同款吗？", model_info=info)
    system = messages[0].content
    assert info in system
    assert "退货政策、售后手册、常见问答、商品FAQ" in system
    assert "X3、T3、L1、C5" in system
    convention = next(line for line in prompts._LABEL.splitlines() if line.startswith("知识库约定："))
    assert convention in system
    assert "宁可多选" in system and "逐段复核" in system
    assert "JSON" in system and '{"answers": [编号, ...]}' in system and '{"answers": []}' in system
    assert "候选段落：\n[1] 申请换货\n只能换同款" in system
    assert messages[-1].content == "顾客问题：X3 换货只能换同款吗？"


def test_prompts_render_with_all_variables():
    assert set(prompts.GEN_PROMPTS) == set(BUCKETS)
    for bucket, prompt in prompts.GEN_PROMPTS.items():
        assert isinstance(prompt, ChatPromptTemplate)
        messages = prompt.format_messages(target_title="T", target_text="正文", distractors="（无）", avoid="（无）")
        joined = "\n".join(m.content for m in messages)
        assert "JSON" in joined and "正文" in joined, bucket
    info = "问题中没有型号。"
    label = prompts.LABEL_PROMPT.format_messages(candidates="[1] a\nb", query="问", model_info=info)
    assert info in label[0].content
    assert "X3、T3、L1、C5" in label[0].content
    assert "按品类列出的规则表，对该品类的所有型号成立。" in label[0].content
    assert "JSON" in label[0].content and "[1] a" in label[0].content and label[-1].content.endswith("问")
    hashes = prompts.prompt_sha256()
    assert set(hashes) == set(BUCKETS) | {"label", "recall", "pair"} and all(len(v) == 64 for v in hashes.values())
