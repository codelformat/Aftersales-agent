from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select

from app.db.engine import get_sessionmaker
from app.db.models import Faq
from app.knowledge.chunking import PATH_SEP, Chunk, parse_markdown
from app.knowledge.milvus import delete_vectors
from app.repositories import knowledge
from app.repositories.knowledge import NewChunk

DOCS_DIR = Path(__file__).resolve().parents[2] / "knowledge" / "docs"
FAQ_SOURCE = "常见问答"
CONTENT_TYPES = ("policy", "faq", "manual")


@dataclass(frozen=True)
class SourceDoc:
    title: str
    content_type: str
    chunks: list[Chunk]
    linked: bool


def load_doc_sources(docs_dir: Path = DOCS_DIR) -> list[SourceDoc]:
    """读取内容类型子目录中的 Markdown。目录类型必须有效，文档名必须全库唯一。"""
    sources = []
    for path in sorted(docs_dir.glob("*/*.md")):
        content_type = path.parent.name
        if content_type not in CONTENT_TYPES:
            raise ValueError("未知的内容类型目录")
        doc = parse_markdown(path.read_text(encoding="utf-8"))
        sources.append(SourceDoc(doc.title, content_type, doc.chunks, linked=True))
    titles = [s.title for s in sources] + [FAQ_SOURCE]
    if len(titles) != len(set(titles)):
        raise ValueError("文档名重复")
    return sources


async def load_faq_source() -> SourceDoc:
    async with get_sessionmaker()() as s:
        rows = list(await s.scalars(select(Faq).order_by(Faq.id)))
    chunks = [
        Chunk(
            category=r.category, questions=r.question, answer=r.answer,
            section_path=f"{FAQ_SOURCE}{PATH_SEP}{r.category}", is_key_clause=False,
        )
        for r in rows
    ]
    return SourceDoc(FAQ_SOURCE, "faq", chunks, linked=False)


async def ingest_source(src: SourceDoc) -> int:
    """在一个事务中写入一份文档。已入库时跳过，返回 0。"""
    async with get_sessionmaker()() as s:
        if await knowledge.has_source(s, src.title):
            return 0
        rows = await knowledge.insert_chunks(s, [
            NewChunk(c.category, c.questions, c.answer, c.section_path, src.content_type, c.is_key_clause)
            for c in src.chunks
        ])
        if src.linked:
            await knowledge.link_chain(s, [r.id for r in rows])
        await s.commit()
        return len(rows)


async def rebuild_source(title: str) -> int:
    """先删 Milvus 向量，再删 MySQL 块。中途中断后可重跑。"""
    async with get_sessionmaker()() as s:
        ids = await knowledge.ids_for_source(s, title)
    await delete_vectors(ids)
    async with get_sessionmaker()() as s:
        await knowledge.delete_ids(s, ids)
        await s.commit()
    return len(ids)


async def ingest_all(docs_dir: Path = DOCS_DIR, *, rebuild: bool = False) -> dict[str, int]:
    sources = load_doc_sources(docs_dir) + [await load_faq_source()]
    if rebuild:
        for src in sources:
            await rebuild_source(src.title)
    return {src.title: await ingest_source(src) for src in sources}
