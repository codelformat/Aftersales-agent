import json

import httpx
import pytest

from scripts.export_snapshots import export_snapshots


@pytest.fixture
def snapshot_export(tmp_path):
    output = tmp_path / "snapshots"
    output.mkdir()
    replay_dir = tmp_path / "replays"
    replay_dir.mkdir()
    rows = [
        {"scene": "sample"},
        {"channel": "api", "event": "chat", "data": {"user_id": "demo"}},
        {"channel": "sse", "event": "session", "data": {"session_id": "42"}},
        {"channel": "api", "event": "chat", "data": {"user_id": "demo", "session_id": "43"}},
        {"channel": "sse", "event": "citations", "data": {"items": [{"chunk_id": 5}]}},
    ]
    (replay_dir / "sample.jsonl").write_text("\n".join(map(json.dumps, rows)))

    def review(rid, question, updated):
        return {"id": rid, "normalized_question": question, "updated_at": updated,
                "created_at": "2026-10-01T00:00:00", "review_status": "待审",
                "occurrence_count": 1, "approved_answer": None, "ai_suggested_answer": "答"}

    reviews = [review(1, "重复问题", "2026-10-09T00:00:00"),
               review(2, "重复问题", "2026-10-10T00:00:00"),
               review(3, "无关问题", "2026-10-10T00:00:00"),
               review(4, "重复问题", "2026-10-11T00:00:00"),
               review(5, "另一问题", "2026-10-09T00:00:00"),
               review(6, "无来源问题", "2026-10-09T00:00:00")]
    responses = {
        "/api/review-queue": reviews,
        "/api/eval-runs": [{"id": 1, "chunk_id": 90}],
        "/api/faith-cases": [{"id": 1, "retrieved_chunks": [{"chunk_id": 91}]}],
        "/api/tool-audit": [{"id": 1, "conversation_id": 42},
                            {"id": 2, "conversation_id": "43"},
                            {"id": 3, "conversation_id": 142},
                            {"id": 4, "conversation_id": None}],
        "/api/conversations": [
            {"session_id": "42", "updated_at": "2026-10-09"},
            {"session_id": "43", "updated_at": "2026-10-10"},
            {"session_id": "142", "updated_at": "2026-10-11"}],
        "/api/conversations/42/messages": [{"id": 1, "content": "录制问题"},
                                           {"id": 2, "conversation_id": 142, "content": "无关"}],
        "/api/conversations/43/messages": [{"id": 3, "content": "续问", "chunk_id": 8}],
        "/api/conversations/142/messages": [{"id": 4, "content": "无关", "chunk_id": 92}],
    }
    for row, cid, chunk in zip(reviews, [42, "42", 142, 142, 43, None], [6, 7, 93, 94, 9, 95]):
        responses[f'/api/review-queue/{row["id"]}'] = {
            **row, "sources": [] if cid is None else [
                {"id": row["id"], "conversation_id": cid, "raw_question": "问", "source": "user_feedback",
                 "reason": "未解决", "created_at": "2026-10-09T00:00:00",
                 "retrieved_chunks": [{"chunk_id": chunk}]}]}
    # A retained review may also contain evidence from an unrelated conversation.
    responses["/api/review-queue/2"]["sources"].append({
        "id": 99, "conversation_id": 142, "retrieved_chunks": [{"chunk_id": 96}]})
    for cid in range(4, 100):
        responses[f"/api/knowledge/chunks/{cid}"] = {
            "id": cid, "answer": "答", "prev_chunk_id": cid - 1 if cid > 4 else None,
            "next_chunk_id": cid + 1 if cid < 99 else None}

    async def run(before_fetch=None):
        def handle(request):
            if before_fetch:
                before_fetch()
            return httpx.Response(200, json=responses[request.url.path])

        async with httpx.AsyncClient(base_url="http://test", transport=httpx.MockTransport(handle)) as client:
            await export_snapshots(client, output, replay_dir)
        return output

    return run, output, responses, replay_dir


@pytest.mark.anyio
async def test_export_cleans_stale_files_before_fetch_and_preserves_strategy(snapshot_export, tmp_path):
    run, output, _, _ = snapshot_export
    strategy = b'{"strategy": "separate export"}\n'
    (output / "strategy-comparison.json").write_bytes(strategy)
    stale = output / "conversations/999/messages.json"
    stale.parent.mkdir(parents=True)
    stale.write_text("[]")
    partial = output / "stale.json.partial"
    partial.write_text("unfinished")
    outside = tmp_path / "outside.json"
    outside.write_text("keep")
    (output / "outside-link").symlink_to(outside)

    def before_fetch():
        assert not stale.exists()
        assert not partial.exists()

    await run(before_fetch)
    assert (output / "strategy-comparison.json").read_bytes() == strategy
    assert outside.read_text() == "keep"
    assert not (output / "outside-link").exists()


@pytest.mark.anyio
async def test_export_only_recorded_conversations_and_messages(snapshot_export):
    run, _, _, _ = snapshot_export
    output = await run()
    assert [r["session_id"] for r in json.loads((output / "conversations.json").read_text())] == ["43", "42"]
    assert {p.parent.name for p in (output / "conversations").glob("*/messages.json")} == {"42", "43"}
    assert json.loads((output / "conversations/42/messages.json").read_text()) == [{"id": 1, "content": "录制问题"}]


@pytest.mark.anyio
async def test_export_tool_audit_requires_exact_recorded_conversation(snapshot_export):
    run, _, _, _ = snapshot_export
    output = await run()
    assert [r["id"] for r in json.loads((output / "tool-audit.json").read_text())] == [1, 2]


@pytest.mark.anyio
async def test_export_reviews_filters_sources_then_keeps_latest_question(snapshot_export):
    run, _, _, _ = snapshot_export
    output = await run()
    assert {r["id"] for r in json.loads((output / "review-queue.json").read_text())} == {2, 5}
    assert {p.stem for p in (output / "review-queue").glob("*.json")} == {"2", "5"}
    assert [s["conversation_id"] for s in json.loads((output / "review-queue/2.json").read_text())["sources"]] == ["42"]


@pytest.mark.anyio
async def test_export_chunks_only_from_recordings_and_retained_conversations(snapshot_export):
    run, _, _, _ = snapshot_export
    output = await run()
    assert {p.stem for p in (output / "knowledge/chunks").glob("*.json")} == {"5", "7", "8", "9"}


@pytest.mark.anyio
async def test_export_with_no_recordings_has_no_conversation_data(snapshot_export):
    run, _, _, replay_dir = snapshot_export
    (replay_dir / "sample.jsonl").unlink()
    output = await run()
    for name in ("conversations", "tool-audit", "review-queue"):
        assert json.loads((output / f"{name}.json").read_text()) == []
    assert not list((output / "knowledge/chunks").glob("*.json"))
