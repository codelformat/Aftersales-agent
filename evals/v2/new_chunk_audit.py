"""从离线 Top-5 排序结果列出新增来源键优先于标注命中的题。"""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from evals.rag_metrics import dedupe
from evals.v2.make_targets import load_keys


def audit(rankings: dict, new_keys: set[str], strategies=None) -> list[dict]:
    rows = []
    by_strategy = rankings["rankings"]
    for strategy in by_strategy if strategies is None else strategies:
        ranked_samples = by_strategy.get(strategy, {})
        for id, sample in rankings["samples"].items():
            if not sample["relevant"] or id not in ranked_samples:
                continue
            top = dedupe(ranked_samples[id])[:5]
            relevant = {k for group in sample["relevant"] for k in group}
            new_rank = next((i for i, key in enumerate(top, 1) if key in new_keys), None)
            first_hit_rank = next((i for i, key in enumerate(top, 1) if key in relevant), None)
            if new_rank is not None and (first_hit_rank is None or new_rank < first_hit_rank):
                rows.append({"strategy": strategy, "id": id, "bucket": sample["bucket"], "query": sample["query"],
                             "new_key": top[new_rank - 1], "new_rank": new_rank, "first_hit_rank": first_hit_rank})
    return rows


def render(rows: list[dict]) -> str:
    def cell(value):
        return str(value).replace("|", "\\|").replace("\n", " ").replace("\r", " ") if value is not None else "—"

    lines = ["| 策略 | 题号 | 桶 | 问题 | 新增来源键 | 新增键位次 | 首个标注命中位次 |",
             "| --- | --- | --- | --- | --- | --- | --- |"]
    fields = ("strategy", "id", "bucket", "query", "new_key", "new_rank", "first_hit_rank")
    lines.extend("| " + " | ".join(cell(row[field]) for field in fields) + " |" for row in rows)
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="离线审计新增小节对旧题排序的影响。")
    parser.add_argument("--rankings", type=Path, required=True)
    parser.add_argument("--old-keys", type=Path, required=True)
    parser.add_argument("--new-keys", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    try:
        rankings = json.loads(args.rankings.read_text(encoding="utf-8"))
        new_keys = set(load_keys(args.new_keys)) - set(load_keys(args.old_keys))
        report = render(audit(rankings, new_keys))
        if args.out is not None:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(report, encoding="utf-8")
        else:
            print(report, end="")
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"新增小节审计失败：{exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
