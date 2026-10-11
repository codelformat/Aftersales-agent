"""编排：出题 → 机械过滤 → 标注补全；淘汰后同一种子重试，再失败按顺序换下一个种子。"""

import asyncio
from collections import Counter
from dataclasses import dataclass, field
import logging
import random

from langchain_core.runnables import Runnable

from app.knowledge.query import Lexicon
from evals.hard_set.corpus import Chunk
from evals.hard_set.filters import check_query
from evals.hard_set.labels import answer_keys, candidates_for, check_labels, model_info, render_candidates, same_topic_keys
from evals.hard_set.seeds import BUCKETS, Seed

logger = logging.getLogger(__name__)
ID_PREFIX = {"H1_sibling": "H1", "H2_colloquial": "H2", "H3_buried": "H3", "H4_specific": "H4"}
SPOTCHECK_POSITIONS = (5, 20, 35, 50)
SPOTCHECK_RANDOM = 4


@dataclass(frozen=True)
class HardSample:
    id: str
    bucket: str
    query: str
    keys: tuple[str, ...]
    target_key: str
    term: str | None


@dataclass
class BucketStats:
    seeds_tried: int = 0
    attempts: int = 0
    rejected: dict[str, int] = field(default_factory=dict)
    errors: int = 0
    replaced: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _Accepted:
    query: str
    keys: tuple[str, ...]
    term: str | None


def _format_keys(keys, by_key) -> str:
    return "\n\n".join(f"{by_key[k].key}\n{by_key[k].text}" for k in keys if k in by_key) or "（无）"


async def _run_seed(seed: Seed, *, by_key, chunks, lexicon, generator, labeler, pair_checker: Runnable,
                    existing: set[str],
                    avoid: list[str], max_attempts: int, stats: BucketStats, rejected: Counter,
                    semaphore: asyncio.Semaphore) -> _Accepted | None:
    target = by_key[seed.target_key]
    inputs = {"target_title": target.title, "target_text": target.text,
              "distractors": _format_keys(seed.distractor_keys, by_key),
              "avoid": "\n".join(avoid) or "（无）"}
    for _ in range(max_attempts):
        stats.attempts += 1
        try:
            async with semaphore:
                got = await generator.ainvoke(inputs)
            parsed = got["parsed"]
            if parsed is None:
                rejected["解析失败"] += 1
                continue
            query = parsed.query.strip()
            reason = check_query(seed.bucket, query, target, term=parsed.term, chunks=chunks,
                                 lexicon=lexicon, existing=existing)
            if reason:
                rejected[reason] += 1
                continue
            cands = candidates_for(query, chunks, lexicon)
            async with semaphore:
                label = await labeler.ainvoke({"candidates": render_candidates(cands), "query": query,
                                              "model_info": model_info(query, lexicon)})
            if label["parsed"] is None:
                rejected["标注解析失败"] += 1
                continue
            keys = answer_keys(label["parsed"].answers, cands)
            if keys is None:
                rejected["标注编号无效"] += 1
                continue
            cand_keys = {c.key for c in cands}
            pair_failed = False
            for key in same_topic_keys(target, chunks):
                if key in keys or key not in cand_keys:
                    continue
                async with semaphore:
                    pair = await pair_checker.ainvoke({"chunk_title": key, "chunk_text": by_key[key].text,
                                                       "query": query, "model_info": model_info(query, lexicon)})
                if pair["parsed"] is None:
                    rejected["复核解析失败"] += 1
                    pair_failed = True
                    break
                if pair["parsed"].answerable:
                    keys.append(key)
            if pair_failed:
                continue
            reason = check_labels(seed.bucket, target, keys, by_key)
            if reason:
                rejected[reason] += 1
                continue
            return _Accepted(query, tuple(keys), parsed.term)
        except Exception:
            logger.exception("合成失败：%s %s", seed.bucket, seed.target_key)
            stats.errors += 1
    return None


async def _bucket(bucket: str, pool: list[Seed], need: int, *, by_key, chunks, lexicon, generator,
                  labeler, pair_checker: Runnable, max_attempts,
                  semaphore) -> tuple[list[tuple[int, Seed, _Accepted]], BucketStats]:
    stats, rejected = BucketStats(), Counter()
    accepted: list[tuple[int, Seed, _Accepted]] = []
    existing: set[str] = set()
    next_i = 0
    while len(accepted) < need and next_i < len(pool):
        batch = list(enumerate(pool[next_i:next_i + need - len(accepted)], start=next_i))
        next_i += len(batch)
        stats.seeds_tried += len(batch)
        snapshot = set(existing)
        results = await asyncio.gather(*(
            _run_seed(seed, by_key=by_key, chunks=chunks, lexicon=lexicon, generator=generator,
                      labeler=labeler, pair_checker=pair_checker, existing=snapshot,
                      avoid=[a.query for _, s, a in accepted if s.target_key == seed.target_key],
                      max_attempts=max_attempts, stats=stats, rejected=rejected, semaphore=semaphore)
            for _, seed in batch))
        for (index, seed), result in zip(batch, results):
            if result is None:
                stats.replaced.append(seed.target_key)
            elif result.query in existing:
                rejected["问题重复"] += 1
                stats.replaced.append(seed.target_key)
            else:
                existing.add(result.query)
                accepted.append((index, seed, result))
    stats.rejected = dict(rejected)
    accepted.sort(key=lambda item: item[0])
    return accepted, stats


async def synthesize(pools: dict[str, list[Seed]], chunks: list[Chunk], lexicon: Lexicon, *,
                     generators: dict[str, Runnable], labeler: Runnable, pair_checker: Runnable,
                     per_bucket: int | dict[str, int] = 60, max_attempts: int = 3,
                     concurrency: int = 4) -> tuple[list[HardSample], dict[str, BucketStats]]:
    by_key = {c.key: c for c in chunks}
    semaphore = asyncio.Semaphore(concurrency)
    samples, all_stats = [], {}
    for bucket in BUCKETS:
        need = per_bucket[bucket] if isinstance(per_bucket, dict) else per_bucket
        if need == 0:
            all_stats[bucket] = BucketStats()
            continue
        accepted, stats = await _bucket(bucket, pools[bucket], need, by_key=by_key, chunks=chunks,
                                        lexicon=lexicon, generator=generators[bucket], labeler=labeler,
                                        pair_checker=pair_checker,
                                        max_attempts=max_attempts, semaphore=semaphore)
        all_stats[bucket] = stats
        samples.extend(HardSample(f"{ID_PREFIX[bucket]}_{n:02d}", bucket, a.query, a.keys, seed.target_key, a.term)
                       for n, (_, seed, a) in enumerate(accepted, 1))
    return samples, all_stats


def spotcheck_ids(samples: list[HardSample], seed: int) -> list[str]:
    ids = sorted(s.id for s in samples)
    fixed = [f"{ID_PREFIX[b]}_{p:02d}" for b in BUCKETS for p in SPOTCHECK_POSITIONS]
    fixed = [i for i in fixed if i in ids]
    rest = [i for i in ids if i not in fixed]
    return fixed + random.Random(seed).sample(rest, min(SPOTCHECK_RANDOM, len(rest)))
