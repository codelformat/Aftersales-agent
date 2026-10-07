import json
import re

from app.config import CHUNK_MAX_CHARS, GENERAL_CATEGORY, OVERLAP_MAX_CHARS, PRODUCT_CATEGORIES
from app.knowledge.chunking import product_category_of
from app.knowledge.ingest import DOCS_DIR, load_doc_sources

LEXICON = DOCS_DIR.parent / "lexicon.json"
MODELS = {
    "蓝牙耳机": ["X3", "X3 Pro", "X5"],
    "羊毛衫": ["W1", "W1 Plus", "W2"],
    "扫地机器人": ["S10", "S10 Max", "S20"],
    "电动牙刷": ["T3", "T3 Pro", "T5"],
    "台灯": ["L1", "L1 Pro", "L2"],
    "保温杯": ["C5", "C5 Plus", "C8"],
    "运动鞋": ["R1", "R1 Pro", "R2"],
    "手机壳": ["K1", "K1 Pro", "K2"],
}


def _manual():
    return next(s for s in load_doc_sources() if s.title == "商品手册")


def test_docs_have_no_you_char():
    for path in DOCS_DIR.glob("*/*.md"):
        assert "邮" not in path.read_text(encoding="utf-8"), path.name


def test_docs_cover_required_structures():
    sources = load_doc_sources()
    assert sorted(s.content_type for s in sources) == ["faq", "manual", "manual", "policy"]
    chunks = [c for s in sources for c in s.chunks]
    assert len(chunks) + 12 >= 49
    assert sum(c.is_key_clause for c in chunks) >= 2
    table_chunks = [c for c in chunks if c.answer.startswith("|")]
    # 大表应按行切成多块。
    assert len(table_chunks) >= 2
    assert all(len(c.answer) <= CHUNK_MAX_CHARS + OVERLAP_MAX_CHARS for c in chunks)


def test_product_manual_sections():
    manual = _manual()
    # 现有切分器会将一级标题下的说明段单独保留为通用块。
    intro = [c for c in manual.chunks if c.section_path == manual.title]
    assert len(intro) == 1
    assert product_category_of(intro[0].section_path) == GENERAL_CATEGORY
    chunks = [c for c in manual.chunks if c.section_path != manual.title]
    cats = {c.section_path.split(" > ")[1] for c in chunks}
    assert cats == set(PRODUCT_CATEGORIES)
    assert all(product_category_of(c.section_path) != GENERAL_CATEGORY for c in chunks)
    text = (DOCS_DIR / "manual" / "商品手册.md").read_text(encoding="utf-8")
    assert re.findall(r"^## (.+)$", text, re.MULTILINE) == list(PRODUCT_CATEGORIES)
    assert sum(c.is_key_clause for c in chunks) >= 2
    for cat, models in MODELS.items():
        headings = [c.questions for c in chunks if c.section_path.split(" > ")[1] == cat]
        for model in models:
            # 短型号不得计入长型号的小节。
            n = len({h for h in headings if h.startswith(model + " ") and not any(
                h.startswith(o + " ") for o in models if o != model and o.startswith(model))})
            assert 4 <= n <= 5, (model, n)


def test_lexicon_models_match_manual_and_are_canonical():
    lex = json.loads(LEXICON.read_text(encoding="utf-8"))
    assert lex["models"] == MODELS
    assert list(lex["models"]) == list(PRODUCT_CATEGORIES)
    text = (DOCS_DIR / "manual" / "商品手册.md").read_text(encoding="utf-8")
    for model in [m for ms in lex["models"].values() for m in ms]:
        assert re.search(rf"(?<![A-Za-z0-9]){re.escape(model)}(?![A-Za-z0-9])", text), model
        if " " in model:
            assert model.replace(" ", "") not in text, model
            assert model.replace(" ", "-") not in text, model


def test_lexicon_synonyms_shape():
    syn = json.loads(LEXICON.read_text(encoding="utf-8"))["synonyms"]
    assert len(syn) >= 15
    assert syn["运费"] and "邮费" in syn["运费"]
    for key, aliases in syn.items():
        assert aliases and key not in aliases
        assert all(isinstance(a, str) and a for a in aliases)
    all_aliases = [a for aliases in syn.values() for a in aliases]
    assert len(all_aliases) == len(set(all_aliases)), "同一俗称只能对应一个标准词"


def test_chunk_count_supports_eval_set():
    # 240 道可答题，平均每个来源键不超过 3 题。
    assert len({c.section_path for s in load_doc_sources() for c in s.chunks}) >= 80
