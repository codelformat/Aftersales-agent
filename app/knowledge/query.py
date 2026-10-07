"""Query 理解：LLM 改写、型号归一、同义词处理。同义词只在检索侧处理，不改入库文本。"""

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from langchain_core.runnables import Runnable

import app.config as config
from app.llm import get_query_rewriter
from app.schemas import QueryPlan

logger = logging.getLogger(__name__)
LEXICON_PATH = Path(__file__).resolve().parents[2] / "knowledge" / "lexicon.json"
_ALNUM = "A-Za-z0-9"


def _model_key(model: str) -> str:
    return re.sub(r"[\s\-]", "", model).lower()


@dataclass(frozen=True)
class Lexicon:
    model_categories: dict[str, str]
    synonyms: dict[str, tuple[str, ...]]
    _model_re: re.Pattern = field(init=False, repr=False, compare=False)
    _alias_re: re.Pattern | None = field(init=False, repr=False, compare=False)

    def __post_init__(self):
        # 长型号优先，保证 "X3 Pro" 不被拆成 "X3"。型号字符之间允许空格或横线。
        keys = sorted({_model_key(m) for m in self.model_categories}, key=len, reverse=True)
        alts = [r"[\s\-]*".join(re.escape(ch) for ch in k) for k in keys]
        object.__setattr__(self, "_model_re", re.compile(
            rf"(?<![{_ALNUM}])(?:{'|'.join(alts)})(?![{_ALNUM}])", re.IGNORECASE))
        aliases = sorted(self.alias_to_key, key=len, reverse=True)
        object.__setattr__(self, "_alias_re", re.compile("|".join(map(re.escape, aliases))) if aliases else None)

    @property
    def canonical(self) -> dict[str, str]:
        return {_model_key(m): m for m in self.model_categories}

    @property
    def alias_to_key(self) -> dict[str, str]:
        mapping = {a: k for k, al in self.synonyms.items() for a in al}
        for key, aliases in self.synonyms.items():
            for alias in aliases:
                # 先匹配重叠后的完整短语，如“七天无理由退货”，避免重复补上“退货”。
                for overlap in range(min(len(alias), len(key)), 0, -1):
                    if alias.endswith(key[:overlap]):
                        mapping.setdefault(alias + key[overlap:], key)
                        break
        # 标准词整体匹配并保留，避免“恢复出厂设置”被再次补上“设置”。
        mapping.update({key: key for key in self.synonyms})
        return mapping


def load_lexicon(path: Path = LEXICON_PATH) -> Lexicon:
    data = json.loads(path.read_text(encoding="utf-8"))
    return Lexicon(
        model_categories={m: cat for cat, models in data["models"].items() for m in models},
        synonyms={k: tuple(v) for k, v in data["synonyms"].items()},
    )


@lru_cache
def get_lexicon() -> Lexicon:
    return load_lexicon()


def normalize_models(text: str, lexicon: Lexicon) -> str:
    canonical = lexicon.canonical
    return lexicon._model_re.sub(lambda m: canonical[_model_key(m.group(0))], text)


def model_category(text: str, lexicon: Lexicon) -> str | None:
    """返回文本中第一个规范型号所属的品类。文本须已做型号归一。"""
    m = lexicon._model_re.search(text)
    return lexicon.model_categories[lexicon.canonical[_model_key(m.group(0))]] if m else None


def dense_query(text: str, lexicon: Lexicon) -> str:
    """把俗称替换为标准词。只替换一遍，不追加。"""
    if lexicon._alias_re is None:
        return text
    mapping = lexicon.alias_to_key
    return lexicon._alias_re.sub(lambda m: mapping[m.group(0)], text)


def bm25_query(text: str, lexicon: Lexicon) -> str:
    """保留原文，追加命中的标准词和全部俗称。"""
    extra: list[str] = []
    for key, aliases in lexicon.synonyms.items():
        if key in text or any(a in text for a in aliases):
            extra += [t for t in (key, *aliases) if t not in text and t not in extra]
    return " ".join([text, *extra])


async def understand(
    question: str, *, rewriter: Runnable | None = None, lexicon: Lexicon | None = None
) -> QueryPlan:
    lex = lexicon or get_lexicon()
    normalized = normalize_models(question, lex)
    # 工厂在 try 之外调用：测试中未替换时立即暴露。
    rewriter = rewriter or get_query_rewriter()
    try:
        result = await asyncio.wait_for(
            rewriter.ainvoke({"question": normalized}), config.QUERY_REWRITE_TIMEOUT_SECONDS
        )
        parsed = result["parsed"]
        if parsed is None:
            raise ValueError(f"改写结果无效：raw={result.get('raw')!r}")
        standard, category = normalize_models(parsed.standard_query, lex), parsed.product_category
    except Exception:
        logger.exception("Query 改写失败，用原话检索")
        standard, category = normalized, None
    # 型号与品类的对应是确定的，覆盖 LLM 的结果。
    category = model_category(standard, lex) or model_category(normalized, lex) or category
    return QueryPlan(standard_query=standard, product_category=category)
