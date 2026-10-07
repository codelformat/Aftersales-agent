"""读取评估集，检查题号、分布与 MySQL 来源键。"""

from collections import Counter
from dataclasses import dataclass
import json
from pathlib import Path
import re

from sqlalchemy import select

from app.db.engine import get_sessionmaker
from app.db.models import KnowledgeChunk
from app.knowledge.retrieval import source_key
from evals.rag_metrics import DIFFICULTIES

SAMPLES_PATH = Path(__file__).resolve().parent / "rag_eval.jsonl"
BUCKET_SIZES = {"A_policy": 70, "B_model": 60, "C_colloquial": 60,
                "D_unanswerable": 60, "E_multi": 50}
DIFFICULTY_SIZES = {"A_policy": (25, 25, 20), "B_model": (20, 20, 20),
                    "C_colloquial": (15, 25, 20), "D_unanswerable": (20, 20, 20),
                    "E_multi": (10, 20, 20)}


@dataclass(frozen=True)
class EvalSample:
    id: str
    bucket: str
    difficulty: str
    query: str
    relevant: tuple[str, ...]


def load_samples(path: Path = SAMPLES_PATH) -> list[EvalSample]:
    samples = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if not all(isinstance(row[key], str) for key in ("id", "bucket", "difficulty", "query")):
            raise ValueError("评估集文本字段类型无效")
        if not isinstance(row["relevant"], list) or not all(isinstance(k, str) for k in row["relevant"]):
            raise ValueError("评估集来源键类型无效")
        samples.append(EvalSample(row["id"], row["bucket"], row["difficulty"], row["query"], tuple(row["relevant"])))
    return samples


def validate(samples: list[EvalSample], known_keys: set[str] | None, *, full: bool = True) -> list[str]:
    errors = []
    seen = set()
    for s in samples:
        if s.id in seen:
            errors.append(f"题号重复：{s.id}")
        seen.add(s.id)
        if not re.fullmatch(r"[A-E]\d{2,3}", s.id) or s.id[:1] != s.bucket[:1]:
            errors.append(f"题号与桶不符：{s.id}（{s.bucket}）")
        if s.bucket not in BUCKET_SIZES:
            errors.append(f"{s.id}：桶无效 {s.bucket}")
        if s.difficulty not in DIFFICULTIES:
            errors.append(f"{s.id}：难度无效 {s.difficulty}")
        if not s.query.strip() or len(s.query) > 512:
            errors.append(f"{s.id}：问题为空或超过 512 字")
        if s.bucket == "D_unanswerable":
            if s.relevant:
                errors.append(f"{s.id}：D 桶来源键必须为空")
        elif not s.relevant:
            errors.append(f"{s.id}：可答题必须有来源键")
        elif s.bucket == "E_multi" and len(set(s.relevant)) < 2:
            errors.append(f"{s.id}：E 桶至少需要 2 个不同来源键")
        if known_keys is not None:
            errors.extend(f"{s.id}：来源键不存在 {key}" for key in dict.fromkeys(s.relevant) if key not in known_keys)
    if full:
        buckets = Counter(s.bucket for s in samples)
        difficulties = Counter((s.bucket, s.difficulty) for s in samples)
        for bucket, expected in BUCKET_SIZES.items():
            if buckets[bucket] != expected:
                errors.append(f"{bucket}：题数 {buckets[bucket]}，应为 {expected}")
            for difficulty, size in zip(DIFFICULTIES, DIFFICULTY_SIZES[bucket]):
                actual = difficulties[(bucket, difficulty)]
                if actual != size:
                    errors.append(f"{bucket}/{difficulty}：题数 {actual}，应为 {size}")
    return errors


async def known_source_keys() -> dict[str, str]:
    async with get_sessionmaker()() as s:
        rows = await s.scalars(select(KnowledgeChunk).where(
            KnowledgeChunk.vectorize_status == "done", KnowledgeChunk.content_type != "mined",
        ).order_by(KnowledgeChunk.id))
        return {source_key(row.section_path or "", row.questions): row.answer[:60] for row in rows}
