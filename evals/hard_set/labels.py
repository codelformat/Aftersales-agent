# evals/hard_set/labels.py
"""标注补全：候选范围等于生产检索的过滤范围，只依赖知识库结构。"""

from app.config import GENERAL_CATEGORY
from app.knowledge.query import Lexicon, model_category, normalize_models
from evals.hard_set.corpus import Chunk

MAX_GROUP_KEYS = 3


def candidates_for(query: str, chunks: list[Chunk], lexicon: Lexicon) -> list[Chunk]:
    # 与生产检索一致：问题中出现型号时，品类由第一个型号决定。
    category = model_category(normalize_models(query, lexicon), lexicon)
    if category is None:
        return list(chunks)
    return [c for c in chunks if c.category in (category, GENERAL_CATEGORY)]


def render_candidates(cands: list[Chunk]) -> str:
    return "\n\n".join(f"[{i}] {c.key}\n{c.text}" for i, c in enumerate(cands, 1))


def answer_keys(numbers: list[int], cands: list[Chunk]) -> list[str] | None:
    if not numbers or any(not 1 <= n <= len(cands) for n in numbers):
        return None
    return list(dict.fromkeys(cands[n - 1].key for n in numbers))


def check_labels(bucket: str, target: Chunk, keys: list[str], by_key: dict[str, Chunk]) -> str | None:
    if target.key not in keys:
        return "目标段落不在答案中"
    if len(keys) > MAX_GROUP_KEYS:
        return "来源键超过 3 个"
    if bucket == "H1_sibling" and any(by_key[k].model not in (None, target.model) for k in keys):
        return "同系列其他型号也能回答"
    if bucket == "H4_specific" and any(by_key[k].model is None for k in keys):
        return "通用段落也能回答"
    return None
