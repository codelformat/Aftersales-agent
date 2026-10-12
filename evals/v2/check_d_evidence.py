"""离线检查两套题集的 D 桶依据，按字面忽略大小写查找关键词。"""

import argparse
from collections import Counter
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from evals.rag_eval_set import SAMPLES_PATH, V2_SAMPLES_PATH, load_samples

EVIDENCE_PATH = Path(__file__).resolve().parent / "d_evidence.jsonl"


def count_hits(keyword: str, texts: list[str]) -> int:
    return sum(text.casefold().count(keyword.casefold()) for text in texts)


def check(evidence: list[dict], d_ids: dict[str, set[str]], texts: list[str]) -> list[str]:
    errors = []
    counts = Counter()
    for row in evidence:
        if not isinstance(row, dict):
            errors.append("D 桶依据必须为对象")
            continue
        name, id = row.get("set"), row.get("id")
        label = f"{name}/{id}"
        if name not in d_ids or id not in d_ids[name]:
            errors.append(f"{label}：不属于 D 题")
        else:
            counts[(name, id)] += 1
        keywords = row.get("keywords")
        if not isinstance(keywords, list) or not 1 <= len(keywords) <= 4 or not all(
                isinstance(k, str) and k.strip() for k in keywords):
            errors.append(f"{label}：keywords 必须为 1–4 个非空字符串")
            continue
        for keyword in keywords:
            hits = count_hits(keyword, texts)
            if hits:
                errors.append(f"{label}：关键词 {keyword!r} 命中 {hits} 次")
    for name, ids in d_ids.items():
        for id in sorted(ids):
            if counts[(name, id)] != 1:
                errors.append(f"{name}/{id}：依据 {counts[(name, id)]} 行，应恰有 1 行")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="离线检查 D 桶无答案依据。")
    parser.add_argument("--evidence", type=Path, default=EVIDENCE_PATH)
    parser.add_argument("--docs", type=Path, default=ROOT / "knowledge" / "docs")
    parser.add_argument("--seed", type=Path, default=ROOT / "db" / "seed.sql")
    parser.add_argument("--base", type=Path, default=SAMPLES_PATH)
    parser.add_argument("--v2", type=Path, default=V2_SAMPLES_PATH)
    args = parser.parse_args()
    try:
        evidence = [json.loads(line) for line in args.evidence.read_text(encoding="utf-8").splitlines() if line.strip()]
        paths = sorted(args.docs.rglob("*.md"))
        if not paths:
            raise ValueError(f"知识库文档为空：{args.docs}")
        texts = [p.read_text(encoding="utf-8") for p in paths] + [args.seed.read_text(encoding="utf-8")]
        d_ids = {name: {s.id for s in load_samples(path) if s.bucket == "D_unanswerable"}
                 for name, path in (("v2", args.v2), ("base", args.base))}
        errors = check(evidence, d_ids, texts)
        for row in evidence:
            if isinstance(row, dict) and isinstance(row.get("keywords"), list):
                for keyword in row["keywords"]:
                    if isinstance(keyword, str) and keyword.strip():
                        print(f"{row.get('set')}/{row.get('id')}：关键词 {keyword!r} 命中 {count_hits(keyword, texts)} 次")
        for error in errors:
            print(error)
        if not errors:
            print("D 桶依据检查通过")
        return 1 if errors else 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"D 桶依据检查失败：{exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
