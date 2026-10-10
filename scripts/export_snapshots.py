"""Export operations lists and replay details using ReplayDataSource filenames.

Usage: uv run python scripts/export_snapshots.py
Strategy comparison is produced separately by evals/export_strategy_compare.py.
"""

import argparse
import asyncio
import json
from pathlib import Path
import shutil

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
    recorded_ids = set()

    for replay in sorted(Path(replay_dir).glob("*.jsonl")):
        for line in replay.read_text(encoding="utf-8").splitlines()[1:]:
            row = json.loads(line)
            data = row["data"]
            chunks.update(chunk_ids(data))
            for key in ("session_id", "conversation_id"):
                if data.get(key) is not None:
                    recorded_ids.add(str(data[key]))
            if row["channel"] == "sse":
                required_chunks.update(chunk_ids(data))
            if row["channel"] == "api" and row["event"] == "chat":
                users.add(data["user_id"])

    # Only remove entries inside the selected snapshot directory. Unlink
    # symlinks without following them; strategy comparison has its own exporter.
    if output_dir.is_symlink():
        raise ValueError("Snapshot output directory must not be a symlink")
    output_dir.mkdir(parents=True, exist_ok=True)
    for entry in output_dir.iterdir():
        if entry.name == "strategy-comparison.json":
            continue
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry)
        else:
            entry.unlink()

    async def fetch(path):
        response = await client.get(path, params=[*httpx.URL(path).params.multi_items(), ("debug", "true")])
        response.raise_for_status()
        return response.json()

    def write(filename, value):
        target = output_dir / f"{filename}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".json.partial")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(target)

    for name, path in OPS_PATHS.items():
        rows = await fetch(path)
        if name == "review-queue":
            latest = {}
            for row in rows:
                detail = await fetch(f'/api/review-queue/{row["id"]}')
                detail["sources"] = [source for source in detail["sources"]
                                     if str(source.get("conversation_id")) in recorded_ids]
                if not detail["sources"]:
                    continue
                question = row["normalized_question"].strip()
                previous = latest.get(question)
                # API timestamps share the same ISO format. Use the ID to break ties.
                if previous is None or (row["updated_at"], row["id"]) > \
                        (previous[0]["updated_at"], previous[0]["id"]):
                    latest[question] = (row, detail)
            rows = sorted((row for row, _ in latest.values()),
                          key=lambda row: (row["updated_at"], row["id"]), reverse=True)
            for row, detail in latest.values():
                write(f'review-queue/{row["id"]}', detail)
                chunks.update(chunk_ids(detail))
        elif name == "tool-audit":
            rows = [row for row in rows if str(row.get("conversation_id")) in recorded_ids]
            chunks.update(chunk_ids(rows))
        write(name, rows)

    for user in sorted(users):
        response = await client.get("/api/conversations", params={"user_id": user, "debug": "true"})
        response.raise_for_status()
        for conversation in response.json():
            cid = str(conversation["session_id"])
            if cid not in recorded_ids:
                continue
            conversations[cid] = conversation
            query = httpx.QueryParams({"user_id": user})
            messages = await fetch(f"/api/conversations/{cid}/messages?{query}")
            # The current API scopes messages by URL and omits conversation_id.
            # Also check explicit IDs if a response includes them.
            messages = [row for row in messages if str(row.get("conversation_id", cid)) == cid]
            write(f"conversations/{cid}/messages", messages)
            chunks.update(chunk_ids(messages))
    write("conversations", sorted(conversations.values(), key=lambda c: c["updated_at"], reverse=True))
    for cid in sorted(chunks):
        # A previous demo's deleted flywheel chunk can remain in historical review
        # evidence. Those cards already contain their question and answer.
        try:
            chunk = await fetch(f"/api/knowledge/chunks/{cid}")
            write(f"knowledge/chunks/{cid}", chunk)
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
