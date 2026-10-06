from app.config import CHUNK_MAX_CHARS, OVERLAP_MAX_CHARS
from app.knowledge.ingest import DOCS_DIR, load_doc_sources


def test_docs_have_no_you_char():
    for path in DOCS_DIR.glob("*/*.md"):
        assert "邮" not in path.read_text(encoding="utf-8"), path.name


def test_docs_cover_required_structures():
    sources = load_doc_sources()
    assert sorted(s.content_type for s in sources) == ["faq", "manual", "policy"]
    chunks = [c for s in sources for c in s.chunks]
    assert len(chunks) + 12 >= 49
    assert sum(c.is_key_clause for c in chunks) >= 2
    table_chunks = [c for c in chunks if c.answer.startswith("|")]
    # 大表应按行切成多块。
    assert len(table_chunks) >= 2
    assert all(len(c.answer) <= CHUNK_MAX_CHARS + OVERLAP_MAX_CHARS for c in chunks)
