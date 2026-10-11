"""合成困难评估集（真实上游 + MySQL）。

uv run python scripts/synth_hard_set.py            # 合成；题集已存在时需要 --force
uv run python scripts/synth_hard_set.py --check    # 只校验冻结文件：格式、题数、来源键、SHA-256
"""

import argparse
import asyncio
from datetime import datetime
import hashlib
import json
import logging
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import get_settings
from app.db.engine import dispose_engine
from app.knowledge.query import load_lexicon
from app.llm import build_extract_model
from evals.hard_set.corpus import load_chunks
from evals.hard_set.labels import MAX_GROUP_KEYS
from evals.hard_set.pipeline import spotcheck_ids, synthesize
from evals.hard_set.prompts import (
    GEN_PROMPTS, LABEL_PROMPT, PAIR_PROMPT, GeneratedQuery, LabelAnswer, PairAnswer, prompt_sha256,
)
from evals.hard_set.seeds import BUCKETS, SEED, build_pools
from evals.rag_eval_set import HARD_SAMPLES_PATH, load_samples, validate

logger = logging.getLogger(__name__)
HARD_PATH = HARD_SAMPLES_PATH
MANIFEST_PATH = HARD_PATH.with_suffix(".manifest.json")
SPOTCHECK_PATH = HARD_PATH.with_suffix(".spotcheck.md")
PER_BUCKET = 60


def build_runnables():
    model = build_extract_model(get_settings())
    gens = {b: p | model.with_structured_output(GeneratedQuery, method="json_mode", include_raw=True)
            for b, p in GEN_PROMPTS.items()}
    labeler = LABEL_PROMPT | model.with_structured_output(LabelAnswer, method="json_mode", include_raw=True)
    pair_checker = PAIR_PROMPT | model.with_structured_output(PairAnswer, method="json_mode", include_raw=True)
    return gens, labeler, pair_checker


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_hash() -> str | None:
    if not MANIFEST_PATH.exists():
        return "缺少 manifest"
    expected = json.loads(MANIFEST_PATH.read_text(encoding="utf-8")).get("jsonl_sha256")
    return None if expected == sha256_file(HARD_PATH) else "SHA-256 与 manifest 不一致"


def _git_commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                              check=True).stdout.strip()
    except Exception:
        return "unknown"


def _spotcheck_md(samples, by_key) -> str:
    wanted = spotcheck_ids(samples, SEED)
    by_id = {s.id: s for s in samples}
    lines = ["# 困难评估集人工抽查", "", "检查三点：像真实顾客的话；标注正确；属于本桶设计。", ""]
    for sid in wanted:
        s = by_id[sid]
        lines += [f"## {s.id}（{s.bucket}）", "", f"问题：{s.query}", ""]
        if s.term:
            lines += [f"术语：{s.term}", ""]
        lines += [f"- `{k}`：{by_key[k].text[:120]}" for k in s.keys] + [""]
    return "\n".join(lines)


async def run(args: argparse.Namespace) -> int:
    if args.check:
        return await _check()
    if HARD_PATH.exists() and not args.force:
        print(f"{HARD_PATH.name} 已存在（冻结题集）。确认要重新合成时加 --force。")
        return 1
    try:
        lexicon = load_lexicon()
        chunks = await load_chunks(lexicon)
        gens, labeler, pair_checker = build_runnables()
        samples, stats = await synthesize(build_pools(chunks), chunks, lexicon, generators=gens,
                                          labeler=labeler, pair_checker=pair_checker, per_bucket=PER_BUCKET,
                                          concurrency=args.concurrency)
    finally:
        await dispose_engine()
    counts = {b: sum(s.bucket == b for s in samples) for b in BUCKETS}
    for bucket in BUCKETS:
        st = stats[bucket]
        print(f"{bucket}：{counts[bucket]}/{PER_BUCKET}，种子 {st.seeds_tried}，尝试 {st.attempts}，"
              f"故障 {st.errors}，淘汰 {st.rejected}")
    if any(counts[b] < PER_BUCKET for b in BUCKETS):
        print("有桶不足 60 题，未写入文件。")
        return 1
    rows = [{"id": s.id, "bucket": s.bucket, "difficulty": "hard", "query": s.query,
             "relevant": [list(s.keys)], "target": s.target_key, "term": s.term} for s in samples]
    HARD_PATH.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    manifest = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "model": get_settings().chat_model, "git_commit": _git_commit(), "seed": SEED,
        "max_group_keys": MAX_GROUP_KEYS, "prompt_sha256": prompt_sha256(),
        "stats": {b: {"accepted": counts[b], "seeds_tried": stats[b].seeds_tried,
                      "attempts": stats[b].attempts, "errors": stats[b].errors,
                      "rejected": stats[b].rejected, "replaced": stats[b].replaced} for b in BUCKETS},
        "jsonl_sha256": sha256_file(HARD_PATH),
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    SPOTCHECK_PATH.write_text(_spotcheck_md(samples, {c.key: c for c in chunks}), encoding="utf-8")
    print(f"写入 {HARD_PATH.name}、{MANIFEST_PATH.name}、{SPOTCHECK_PATH.name}")
    return 0


async def _check() -> int:
    try:
        keys = {c.key for c in await load_chunks(load_lexicon())}
    finally:
        await dispose_engine()
    errors = validate(load_samples(HARD_PATH), keys, full=True, eval_set="hard")
    hash_error = check_hash()
    if hash_error:
        errors.append(hash_error)
    for error in errors:
        print(error)
    if not errors:
        print("困难题集检查通过")
    return 1 if errors else 0


def _positive(value: str) -> int:
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("参数必须为正整数")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="合成困难评估集，会调用上游模型。")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--force", action="store_true", help="覆盖已存在的题集")
    parser.add_argument("--concurrency", type=_positive, default=4)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    logging.basicConfig(level=logging.ERROR)
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
