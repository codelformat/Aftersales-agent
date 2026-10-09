"""按意图汇总 Langfuse 中 chat_turn 的 token 用量。只统计 token。

用法：uv run python scripts/intent_cost.py --days 7
"""

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from app.config import get_settings
from app.observability import TRACE_NAME

PAGE_LIMIT = 1000
NO_INTENT = "-"


@dataclass(frozen=True)
class Obs:
    trace_id: str
    is_root: bool
    intent: str | None
    input: int
    output: int
    total: int


@dataclass(frozen=True)
class IntentCost:
    intent: str
    turns: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    avg_tokens: float
    share: float


def parse_observation(raw: dict) -> Obs:
    usage = raw.get("usageDetails") or {}
    metadata = raw.get("metadata") or {}
    input_tokens = sum(int(value) for key, value in usage.items() if key.startswith("input"))
    output_tokens = sum(int(value) for key, value in usage.items() if key.startswith("output"))
    return Obs(
        raw["traceId"],
        raw.get("isRootObservation", raw.get("parentObservationId") is None),
        metadata.get("intent") if isinstance(metadata, dict) else None,
        input_tokens,
        output_tokens,
        int(usage.get("total", input_tokens + output_tokens)),
    )


def aggregate(roots: list[Obs], generations: list[Obs]) -> list[IntentCost]:
    intent_of: dict[str, str] = {}
    for root in roots:
        intent_of.setdefault(root.trace_id, NO_INTENT)
        # 恢复时可产生多个根。已识别的意图优先于缺失值。
        if root.intent and root.intent != NO_INTENT and intent_of[root.trace_id] == NO_INTENT:
            intent_of[root.trace_id] = root.intent

    tokens = {trace_id: [0, 0, 0] for trace_id in intent_of}
    for generation in generations:
        if generation.trace_id not in tokens:
            continue
        totals = tokens[generation.trace_id]
        totals[0] += generation.input
        totals[1] += generation.output
        totals[2] += generation.total

    groups: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0, 0])
    for trace_id, (input_tokens, output_tokens, total_tokens) in tokens.items():
        group = groups[intent_of[trace_id]]
        group[0] += 1
        group[1] += input_tokens
        group[2] += output_tokens
        group[3] += total_tokens
    grand_total = sum(group[3] for group in groups.values()) or 1
    rows = [
        IntentCost(intent, group[0], group[1], group[2], group[3],
                   group[3] / group[0], group[3] / grand_total)
        for intent, group in groups.items()
    ]
    return sorted(rows, key=lambda row: (-row.total_tokens, row.intent))


def render(rows: list[IntentCost], days: int) -> str:
    lines = [
        f"# 最近 {days} 天按意图的 token 用量（trace 名 {TRACE_NAME}）", "",
        "| 意图 | 轮数 | 输入 token（含缓存） | 输出 token（含思考） | 总 token | 每轮平均 | 占比 |",
        "|---|---|---|---|---|---|---|",
    ]
    for index, row in enumerate(rows):
        mark = "（最费 token）" if index == 0 else ""
        lines.append(
            f"| {row.intent}{mark} | {row.turns} | {row.input_tokens} | {row.output_tokens} "
            f"| {row.total_tokens} | {row.avg_tokens:.0f} | {row.share:.1%} |"
        )
    return "\n".join(lines)


def fetch(base_url: str, auth: tuple[str, str], days: int) -> tuple[list[Obs], list[Obs]]:
    """分别读取根和 GENERATION。两次查询使用同一 UTC 时间窗。"""
    now = datetime.now(timezone.utc)
    common = {
        "fromStartTime": (now - timedelta(days=days)).isoformat(),
        "toStartTime": now.isoformat(),
        "limit": PAGE_LIMIT,
    }
    queries = [
        {"isRootObservation": "true", "fields": "core,basic,metadata",
         "filter": json.dumps([
             {"type": "string", "column": "traceName", "operator": "=", "value": TRACE_NAME},
         ])},
        {"fields": "core,usage", "filter": json.dumps([
            {"type": "string", "column": "type", "operator": "=", "value": "GENERATION"},
        ])},
    ]
    roots: list[Obs] = []
    generations: list[Obs] = []
    # 本机 shell 有 HTTP_PROXY。访问本机 Langfuse 不走代理。
    with httpx.Client(trust_env=False, auth=auth, timeout=30) as client:
        for query, observations in zip(queries, (roots, generations)):
            cursor = None
            while True:
                params = {**common, **query}
                if cursor:
                    params["cursor"] = cursor
                response = client.get(f"{base_url.rstrip('/')}/api/public/v2/observations", params=params)
                response.raise_for_status()
                body = response.json()
                observations.extend(parse_observation(raw) for raw in body.get("data", []))
                cursor = (body.get("meta") or {}).get("cursor")
                if not cursor:
                    break
    return roots, generations


def main() -> int:
    parser = argparse.ArgumentParser(description="按意图汇总 token 用量（读 Langfuse）。")
    parser.add_argument("--days", type=int, default=7)
    args = parser.parse_args()
    settings = get_settings()
    if not (settings.langfuse_public_key and settings.langfuse_secret_key and settings.langfuse_base_url):
        print("未配置 Langfuse（LANGFUSE_PUBLIC_KEY、LANGFUSE_SECRET_KEY、LANGFUSE_BASE_URL）")
        return 1
    roots, generations = fetch(
        settings.langfuse_base_url,
        (settings.langfuse_public_key, settings.langfuse_secret_key.get_secret_value()),
        args.days,
    )
    rows = aggregate(roots, generations)
    print(render(rows, args.days) if rows else "时间窗内没有 chat_turn 数据")
    return 0


if __name__ == "__main__":
    sys.exit(main())
