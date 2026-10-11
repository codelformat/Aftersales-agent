"""读取评估集，检查题号、分布与 MySQL 来源键。"""

from collections import Counter
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re

from sqlalchemy import select

from app.db.engine import get_sessionmaker
from app.db.models import KnowledgeChunk
from app.knowledge.retrieval import source_key
from evals.rag_metrics import DIFFICULTIES

SAMPLES_PATH = Path(__file__).resolve().parent / "rag_eval.jsonl"
V2_SAMPLES_PATH = SAMPLES_PATH.parent / "rag_eval_v2.jsonl"
V2_MANIFEST_PATH = SAMPLES_PATH.parent / "v2" / "manifest.json"
BUCKET_SIZES = {"A_policy": 70, "B_model": 60, "C_colloquial": 60,
                "D_unanswerable": 60, "E_multi": 50}
DIFFICULTY_SIZES = {"A_policy": (25, 25, 20), "B_model": (20, 20, 20),
                    "C_colloquial": (15, 25, 20), "D_unanswerable": (20, 20, 20),
                    "E_multi": (10, 20, 20)}
V2_BUCKET_SIZES = {"A_policy": 40, "B_model": 50, "C_colloquial": 50,
                   "D_unanswerable": 60, "E_multi": 100}
V2_DIFFICULTY_SIZES = {"A_policy": (0, 20, 20), "B_model": (10, 20, 20),
                       "C_colloquial": (10, 20, 20), "D_unanswerable": (15, 20, 25),
                       "E_multi": (20, 40, 40)}
V2_MIN_THREE_GROUP_E = 50


@dataclass(frozen=True)
class EvalSample:
    id: str
    bucket: str
    difficulty: str
    query: str
    relevant: tuple[tuple[str, ...], ...]


def load_samples(path: Path = SAMPLES_PATH) -> list[EvalSample]:
    samples = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if not all(isinstance(row[key], str) for key in ("id", "bucket", "difficulty", "query")):
            raise ValueError("评估集文本字段类型无效")
        groups = row["relevant"]
        if not isinstance(groups, list) or not all(
                isinstance(g, list) and all(isinstance(k, str) for k in g) for g in groups):
            raise ValueError("评估集来源键类型无效")
        samples.append(EvalSample(row["id"], row["bucket"], row["difficulty"], row["query"],
                                  tuple(tuple(g) for g in groups)))
    return samples


def validate(samples: list[EvalSample], known_keys: set[str] | None, *, full: bool = True,
             eval_set: str = "base") -> list[str]:
    if eval_set not in ("base", "v2"):
        raise ValueError("题集必须为 base 或 v2")
    v2 = eval_set == "v2"
    bucket_sizes = V2_BUCKET_SIZES if v2 else BUCKET_SIZES
    difficulty_sizes = V2_DIFFICULTY_SIZES if v2 else DIFFICULTY_SIZES
    id_pattern = r"[A-E]\d{3}" if v2 else r"[A-E]\d{2,3}"
    errors = []
    seen = set()
    for s in samples:
        if s.id in seen:
            errors.append(f"题号重复：{s.id}")
        seen.add(s.id)
        if not re.fullmatch(id_pattern, s.id) or s.id[:1] != s.bucket[:1]:
            errors.append(f"题号与桶不符：{s.id}（{s.bucket}）")
        if s.bucket not in bucket_sizes:
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
        elif v2 and s.bucket in ("A_policy", "B_model", "C_colloquial") and len(s.relevant) != 1:
            errors.append(f"{s.id}：A、B、C 桶必须恰好 1 组事实")
        elif v2 and s.bucket == "E_multi" and len(s.relevant) not in (2, 3):
            errors.append(f"{s.id}：E 桶必须为 2 或 3 组事实")
        elif s.bucket == "E_multi" and len(s.relevant) < 2:
            errors.append(f"{s.id}：E 桶至少需要 2 组事实")
        for group in s.relevant:
            if not group:
                errors.append(f"{s.id}：来源键组为空")
            elif len(set(group)) != len(group):
                errors.append(f"{s.id}：来源键组内重复")
        if known_keys is not None:
            errors.extend(f"{s.id}：来源键不存在 {key}"
                          for key in dict.fromkeys(k for g in s.relevant for k in g) if key not in known_keys)
    if full:
        buckets = Counter(s.bucket for s in samples)
        difficulties = Counter((s.bucket, s.difficulty) for s in samples)
        for bucket, expected in bucket_sizes.items():
            if buckets[bucket] != expected:
                errors.append(f"{bucket}：题数 {buckets[bucket]}，应为 {expected}")
            for difficulty, size in zip(DIFFICULTIES, difficulty_sizes[bucket]):
                actual = difficulties[(bucket, difficulty)]
                if actual != size:
                    errors.append(f"{bucket}/{difficulty}：题数 {actual}，应为 {size}")
        if v2:
            three_groups = sum(s.bucket == "E_multi" and len(s.relevant) == 3 for s in samples)
            if three_groups < V2_MIN_THREE_GROUP_E:
                errors.append(f"E_multi：3 组的题 {three_groups} 道，至少 {V2_MIN_THREE_GROUP_E}")
    return errors


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_manifest(samples_path: Path = V2_SAMPLES_PATH,
                   manifest_path: Path = V2_MANIFEST_PATH) -> str | None:
    if not manifest_path.exists():
        return "v2 题集未冻结：缺少 manifest"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("samples_sha256") != sha256_file(samples_path):
        return "v2 题集 SHA-256 与 manifest 不一致"
    return None


async def known_source_keys() -> dict[str, str]:
    async with get_sessionmaker()() as s:
        rows = await s.scalars(select(KnowledgeChunk).where(
            KnowledgeChunk.vectorize_status == "done", KnowledgeChunk.content_type != "mined",
        ).order_by(KnowledgeChunk.id))
        return {source_key(row.section_path or "", row.questions): row.answer[:60] for row in rows}
