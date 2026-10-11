# evals/hard_set/filters.py
"""困难评估集的机械过滤。纯函数，不访问外部服务，不看检索结果。"""

import re

from app.knowledge.query import Lexicon, normalize_models
from evals.hard_set.corpus import Chunk

MAX_QUERY_CHARS = 200
H3_MIN_CHARS = 60
H3_MAX_CHARS = 150
H2_BODY_MAX_OVERLAP = 0.15
TERM_MIN_CHARS = 2
TERM_MAX_CHARS = 8
TERM_MAX_KEYS = 3


def models_in(text: str, lexicon: Lexicon) -> list[str]:
    normalized = normalize_models(text, lexicon)
    canonical = lexicon.canonical
    found = []
    for m in lexicon._model_re.finditer(normalized):
        model = canonical[re.sub(r"[\s\-]", "", m.group(0)).lower()]
        if model not in found:
            found.append(model)
    return found


def _bigrams(text: str, lexicon: Lexicon) -> set[str]:
    stripped = lexicon._model_re.sub("", normalize_models(text, lexicon))
    chars = re.sub(r"[\W_]", "", stripped)
    return {chars[i:i + 2] for i in range(len(chars) - 1)}


def bigram_overlap(query: str, text: str, lexicon: Lexicon) -> float:
    q = _bigrams(query, lexicon)
    if not q:
        return 0.0
    return len(q & _bigrams(text, lexicon)) / len(q)


def check_query(bucket: str, query: str, target: Chunk, *, term: str | None,
                chunks: list[Chunk], lexicon: Lexicon, existing: set[str]) -> str | None:
    q = query.strip()
    if not q:
        return "问题为空"
    if len(q) > MAX_QUERY_CHARS:
        return "问题超过 200 字"
    if q in existing:
        return "问题重复"
    if bucket in ("H1_sibling", "H4_specific"):
        models = models_in(q, lexicon)
        if target.model not in models:
            return "缺少目标型号"
        if len(models) > 1:
            return "含其他型号"
    if bucket == "H2_colloquial":
        if bigram_overlap(q, target.title, lexicon) > 0:
            return "与标题字面重合"
        if bigram_overlap(q, target.text, lexicon) > H2_BODY_MAX_OVERLAP:
            return "与正文字面重合过高"
    if bucket == "H3_buried":
        if not H3_MIN_CHARS <= len(q) <= H3_MAX_CHARS:
            return "长度不在 60–150 字"
        if not term or not TERM_MIN_CHARS <= len(term) <= TERM_MAX_CHARS:
            return "术语长度无效"
        if term not in target.text and term not in target.title:
            return "术语不在目标段落中"
        if q.count(term) != 1:
            return "术语在问题中出现次数不是 1"
        if sum(term in c.text or term in c.title for c in chunks) > TERM_MAX_KEYS:
            return "术语不稀有"
    return None
