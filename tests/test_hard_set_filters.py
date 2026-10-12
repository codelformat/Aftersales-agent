# tests/test_hard_set_filters.py
import pytest

from app.knowledge.query import load_lexicon
from evals.hard_set import filters as f
from evals.hard_set.corpus import Chunk

LEX = load_lexicon()


def chunk(key="商品手册 > 蓝牙耳机 > X3 Pro 续航", title="X3 Pro 续航", text="X3 Pro 关闭降噪单次续航约 8 小时。",
          category="蓝牙耳机", source="商品手册", model="X3 Pro", aspect="续航"):
    return Chunk(key, key, title, text, category, source, model, aspect)


def check(bucket, query, target=None, term=None, chunks=None, existing=()):
    target = target or chunk()
    return f.check_query(bucket, query, target, term=term, chunks=chunks or [target],
                         lexicon=LEX, existing=set(existing))


@pytest.mark.parametrize("text, expected", [
    ("x3pro 开降噪能用多久", ["X3 Pro"]),
    ("X3-Pro 和 X3 哪个好", ["X3 Pro", "X3"]),
    ("X3pro 续航", ["X3 Pro"]),
    ("耳机就左边响", []),
])
def test_models_in_uses_lexicon_aliases(text, expected):
    assert f.models_in(text, LEX) == expected


def test_bigram_overlap_strips_models_and_punctuation():
    assert f.bigram_overlap("X3 Pro 续航？", "X3 Pro 续航", LEX) == 1.0
    assert f.bigram_overlap("X3 Pro 能撑多久", "X3 Pro 续航", LEX) == 0.0
    assert f.bigram_overlap("", "续航", LEX) == 0.0


def test_common_rules():
    assert check("H1_sibling", " ") == "问题为空"
    assert check("H1_sibling", "X3 Pro" + "长" * 200) == "问题超过 200 字"
    assert check("H1_sibling", "X3 Pro 开降噪能撑多久", existing={"X3 Pro 开降噪能撑多久"}) == "问题重复"


@pytest.mark.parametrize("bucket", ["H1_sibling", "H4_specific"])
def test_model_rules(bucket):
    assert check(bucket, "x3pro 开降噪能撑多久") is None
    assert check(bucket, "这副耳机开降噪能撑多久") == "缺少目标型号"
    assert check(bucket, "X3 Pro 比 X5 能撑多久") == "含其他型号"
    assert check(bucket, "X3 开降噪能撑多久") == "缺少目标型号"


def test_h2_overlap_rules():
    fault = chunk(key="商品手册 > 蓝牙耳机 > X3 常见故障", title="X3 常见故障",
                  text="X3 只有一边有声音时，把两只耳机放回充电盒，盖上盖子等 10 秒。", model="X3", aspect="常见故障")
    assert check("H2_colloquial", "X3 戴着左边响右边哑了咋整", target=fault) is None
    assert check("H2_colloquial", "X3 常见的毛病有哪些", target=fault) == "与标题字面重合"
    assert check("H2_colloquial", "只有一边有声音时把两只耳机放回充电盒行不行", target=fault) == "与正文字面重合过高"


def test_h3_rules():
    target = chunk(key="退货政策 > 特殊情况 > 价格保护", title="价格保护",
                   text="签收后 7 天内降价的，可以申请价格保护。", category="通用", source="退货政策",
                   model=None, aspect=None)
    filler = "我上周在你们这买了好几样东西，有给家里老人的保温杯，也有给孩子挑的运动鞋，快递第二天就到了，包装也挺严实，"
    ok = filler + "就是想问下价格保护怎么弄。"
    assert 60 <= len(ok) <= 150
    assert check("H3_buried", ok, target=target, term="价格保护") is None
    assert check("H3_buried", "价格保护怎么弄", target=target, term="价格保护") == "长度不在 60–150 字"
    assert check("H3_buried", ok, target=target, term=None) == "术语长度无效"
    assert check("H3_buried", ok, target=target, term="很久以前") == "术语不在目标段落中"
    twice = filler + "价格保护怎么弄，价格保护要多久？"
    assert check("H3_buried", twice, target=target, term="价格保护") == "术语在问题中出现次数不是 1"
    others = [target] + [chunk(key=f"k{i}", text="价格保护说明", model=None) for i in range(3)]
    assert check("H3_buried", ok, target=target, term="价格保护", chunks=others) == "术语不稀有"
