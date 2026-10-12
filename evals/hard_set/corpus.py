"""困难评估集的知识库视图：按来源键合并分块，解析型号和方面。不导入检索模块。"""

from dataclasses import dataclass

from sqlalchemy import select

from app.config import GENERAL_CATEGORY
from app.db.engine import get_sessionmaker
from app.db.models import KnowledgeChunk
from app.knowledge.chunking import PATH_SEP, product_category_of
from app.knowledge.query import Lexicon

# 与 app.knowledge.retrieval.FAQ_TABLE_SOURCE 一致，测试比对两者。
FAQ_TABLE_SOURCE = "常见问答"
DOC_CONTENT_TYPES = ("manual", "faq", "policy")
# 商品手册约定：商品FAQ 中未注明型号的参数对应这 4 个型号。
DEFAULT_MODELS = ("X3", "T3", "L1", "C5")


@dataclass(frozen=True)
class Chunk:
    key: str
    section_path: str
    title: str
    text: str
    category: str
    source: str
    model: str | None
    aspect: str | None


def chunk_key(section_path: str, questions: str) -> str:
    """与 app.knowledge.retrieval.source_key 同一规则。"""
    if section_path.startswith(FAQ_TABLE_SOURCE + PATH_SEP):
        return f"{section_path}{PATH_SEP}{questions}"
    return section_path


def _model_aspect(section_path: str, lexicon: Lexicon) -> tuple[str | None, str | None]:
    parts = section_path.split(PATH_SEP)
    if parts[0] != "商品手册" or len(parts) != 3:
        return None, None
    last = parts[2]
    for model in sorted(lexicon.model_categories, key=len, reverse=True):
        if last.startswith(model + " "):
            return model, last[len(model) + 1:].strip()
    return None, None


def build_chunks(rows: list[tuple[str, str, str]], lexicon: Lexicon) -> list[Chunk]:
    order: list[str] = []
    merged: dict[str, dict] = {}
    for section_path, questions, answer in rows:
        path = section_path or ""
        key = chunk_key(path, questions)
        if key not in merged:
            order.append(key)
            title = questions if path.startswith(FAQ_TABLE_SOURCE + PATH_SEP) else path.split(PATH_SEP)[-1]
            merged[key] = {"path": path, "title": title, "texts": []}
        merged[key]["texts"].append(answer)
    out = []
    for key in order:
        item = merged[key]
        model, aspect = _model_aspect(item["path"], lexicon)
        out.append(Chunk(key=key, section_path=item["path"], title=item["title"],
                         text="\n".join(item["texts"]),
                         category=product_category_of(item["path"]) or GENERAL_CATEGORY,
                         source=item["path"].split(PATH_SEP)[0], model=model, aspect=aspect))
    return out


async def load_chunks(lexicon: Lexicon) -> list[Chunk]:
    async with get_sessionmaker()() as s:
        rows = (await s.execute(
            select(KnowledgeChunk.section_path, KnowledgeChunk.questions, KnowledgeChunk.answer)
            .where(KnowledgeChunk.vectorize_status == "done",
                   KnowledgeChunk.content_type.in_(DOC_CONTENT_TYPES))
            .order_by(KnowledgeChunk.id)
        )).all()
    return build_chunks([tuple(r) for r in rows], lexicon)
