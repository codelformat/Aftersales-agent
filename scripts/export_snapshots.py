"""Export operations lists and replay details using ReplayDataSource filenames.

Usage: uv run python scripts/export_snapshots.py
Strategy comparison is produced separately by evals/export_strategy_compare.py.
"""

import argparse
import asyncio
from collections import deque
import json
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
OPS_PATHS = {
    "review-queue": "/api/review-queue", "eval-runs": "/api/eval-runs",
    "faith-cases": "/api/faith-cases", "tool-audit": "/api/tool-audit?limit=500",
}


def chunk_ids(value):
    if isinstance(value, dict):
        if isinstance(value.get("chunk_id"), int):
            yield value["chunk_id"]
        for child in value.values():
            yield from chunk_ids(child)
    elif isinstance(value, list):
        for child in value:
            yield from chunk_ids(child)


async def export_snapshots(client, output_dir, replay_dir):
    output_dir = Path(output_dir)
    chunks, required_chunks, users, conversations = set(), set(), set(), {}

    async def fetch(path, filename):
        response = await client.get(path, params=[*httpx.URL(path).params.multi_items(), ("debug", "true")])
        response.raise_for_status()
        value = response.json()
        target = output_dir / f"{filename}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".json.partial")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(target)
        chunks.update(chunk_ids(value))
        return value

    for name, path in OPS_PATHS.items():
        rows = await fetch(path, name)
        if name == "review-queue":
            for row in rows:
                await fetch(f'/api/review-queue/{row["id"]}', f'review-queue/{row["id"]}')

    for replay in sorted(Path(replay_dir).glob("*.jsonl")):
        for line in replay.read_text(encoding="utf-8").splitlines()[1:]:
            row = json.loads(line)
            chunks.update(chunk_ids(row["data"]))
            if row["channel"] == "sse":
                required_chunks.update(chunk_ids(row["data"]))
            if row["channel"] == "api" and row["event"] == "chat":
                users.add(row["data"]["user_id"])
    for user in sorted(users):
        response = await client.get("/api/conversations", params={"user_id": user, "debug": "true"})
        response.raise_for_status()
        for conversation in response.json():
            cid = conversation["session_id"]
            conversations[cid] = conversation
            query = httpx.QueryParams({"user_id": user})
            await fetch(f"/api/conversations/{cid}/messages?{query}", f"conversations/{cid}/messages")
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "conversations.json").write_text(
        json.dumps(sorted(conversations.values(), key=lambda c: c["updated_at"], reverse=True),
                   ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    pending, visited = deque(sorted(chunks)), set()
    while pending:
        cid = pending.popleft()
        if cid in visited:
            continue
        visited.add(cid)
        # A previous demo's deleted flywheel chunk can remain in historical review
        # evidence. Those cards already contain their question and answer.
        try:
            chunk = await fetch(f"/api/knowledge/chunks/{cid}", f"knowledge/chunks/{cid}")
            # The original-text dialog can navigate to adjacent chunks.
            for key in ("prev_chunk_id", "next_chunk_id"):
                neighbor = chunk.get(key)
                if neighbor is not None:
                    required_chunks.add(neighbor)
                    pending.append(neighbor)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404 or cid in required_chunks:
                raise
            print(f"Historical chunk {cid} no longer exists; retained inline evidence")


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "web/public/snapshots")
    parser.add_argument("--replay-dir", type=Path, default=ROOT / "web/public/replays")
    args = parser.parse_args()
    async with httpx.AsyncClient(base_url=args.base_url, trust_env=False, timeout=30) as client:
        await export_snapshots(client, args.output_dir, args.replay_dir)
    print(f"Exported snapshots to {args.output_dir}")


if __name__ == "__main__":
    asyncio.run(main())
