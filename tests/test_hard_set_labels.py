# tests/test_hard_set_labels.py
from langchain_core.prompts import ChatPromptTemplate

from app.knowledge.query import load_lexicon
from evals.hard_set import labels, prompts
from evals.hard_set.corpus import Chunk
from evals.hard_set.seeds import BUCKETS

LEX = load_lexicon()


def c(key, category, model=None, source="商品手册"):
    return Chunk(key, key, key.split(" > ")[-1], f"{key} 正文", category, source, model, None)


CHUNKS = [
    c("商品手册 > 蓝牙耳机 > X3 续航", "蓝牙耳机", "X3"),
    c("商品手册 > 蓝牙耳机 > X3 Pro 续航", "蓝牙耳机", "X3 Pro"),
    c("商品手册 > 台灯 > L1 参数", "台灯", "L1"),
    c("商品FAQ > 蓝牙耳机 > 耳机充满电能用多久？", "蓝牙耳机", None, "商品FAQ"),
    c("售后手册 > 维修 > 保修期", "通用", None, "售后手册"),
]
BY_KEY = {x.key: x for x in CHUNKS}


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


def test_prompts_render_with_all_variables():
    assert set(prompts.GEN_PROMPTS) == set(BUCKETS)
    for bucket, prompt in prompts.GEN_PROMPTS.items():
        assert isinstance(prompt, ChatPromptTemplate)
        messages = prompt.format_messages(target_title="T", target_text="正文", distractors="（无）", avoid="（无）")
        joined = "\n".join(m.content for m in messages)
        assert "JSON" in joined and "正文" in joined, bucket
    label = prompts.LABEL_PROMPT.format_messages(candidates="[1] a\nb", query="问")
    assert "JSON" in label[0].content and "[1] a" in label[0].content and label[-1].content.endswith("问")
    hashes = prompts.prompt_sha256()
    assert set(hashes) == set(BUCKETS) | {"label"} and all(len(v) == 64 for v in hashes.values())
