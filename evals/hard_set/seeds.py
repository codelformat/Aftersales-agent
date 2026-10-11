"""确定性生成 4 个桶的种子池。只看知识库结构，不看检索结果。"""

from collections import defaultdict
from dataclasses import dataclass
import random

from app.config import GENERAL_CATEGORY
from evals.hard_set.corpus import DEFAULT_MODELS, Chunk

SEED = 20261010
BUCKETS = ("H1_sibling", "H2_colloquial", "H3_buried", "H4_specific")
# 简介段和整张品类表不出题。
EXCLUDED_KEYS = frozenset({"商品手册", "售后手册", "退货政策", "退货政策 > 品类退换货规则"})
H3_SOURCES = ("退货政策", "售后手册", "常见问答", "商品FAQ")
WARRANTY_DISTRACTORS = ("售后手册 > 维修 > 保修期",)
RETURN_DISTRACTORS = ("退货政策 > 退货条件 > 七天无理由退货", "退货政策 > 退货条件 > 质量问题退货",
                      "退货政策 > 品类退换货规则")
POLICY_ASPECTS = ("保修", "退换规则")


@dataclass(frozen=True)
class Seed:
    bucket: str
    target_key: str
    slot: int
    distractor_keys: tuple[str, ...]


def _h4_distractors(target: Chunk, chunks: list[Chunk]) -> tuple[str, ...]:
    keys = {c.key for c in chunks}
    if target.aspect == "保修":
        extra = [c.key for c in chunks if c.key.startswith("常见问答 > 售后维修 > ")]
        return tuple(k for k in WARRANTY_DISTRACTORS if k in keys) + tuple(extra)
    if target.aspect == "退换规则":
        extra = [c.key for c in chunks if c.key.startswith("常见问答 > 退换货 > 退货政策")]
        return tuple(k for k in RETURN_DISTRACTORS if k in keys) + tuple(extra)
    return tuple(c.key for c in chunks if c.source == "商品FAQ" and c.category == target.category)


def _targets(bucket: str, chunks: list[Chunk]) -> list[tuple[Chunk, tuple[str, ...]]]:
    default_categories = {c.category for c in chunks if c.model in DEFAULT_MODELS}
    out = []
    for c in chunks:
        if c.key in EXCLUDED_KEYS:
            continue
        if bucket == "H1_sibling" and c.model:
            siblings = tuple(o.key for o in chunks if o.model and o.model != c.model
                             and o.category == c.category and o.aspect == c.aspect)
            if siblings:
                out.append((c, siblings))
        elif bucket == "H2_colloquial" and (
                (c.model and c.aspect == "常见故障") or c.source == "商品FAQ"
                or (c.category == GENERAL_CATEGORY and c.source != "商品手册")):
            out.append((c, ()))
        elif bucket == "H3_buried" and c.source in H3_SOURCES:
            out.append((c, ()))
        elif bucket == "H4_specific" and c.model and (
                c.aspect in POLICY_ASPECTS
                or (c.model not in DEFAULT_MODELS and c.category in default_categories
                    and c.aspect != "常见故障")):
            distractors = _h4_distractors(c, chunks)
            if distractors:
                out.append((c, distractors))
    return out


def _round_robin(items: list[tuple[Chunk, tuple[str, ...]]], rng_key: str) -> list[tuple[Chunk, tuple[str, ...]]]:
    groups = defaultdict(list)
    for item in items:
        groups[item[0].category].append(item)
    names = sorted(groups)
    for name in names:
        random.Random(f"{rng_key}:{name}").shuffle(groups[name])
    out = []
    while any(groups[n] for n in names):
        for name in names:
            if groups[name]:
                out.append(groups[name].pop(0))
    return out


def build_pools(chunks: list[Chunk], *, seed: int = SEED) -> dict[str, list[Seed]]:
    pools = {}
    for bucket in BUCKETS:
        ordered = _round_robin(_targets(bucket, chunks), f"{seed}:{bucket}")
        pools[bucket] = [Seed(bucket, c.key, slot, d) for slot in (1, 2) for c, d in ordered]
    return pools
