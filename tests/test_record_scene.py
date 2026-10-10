import json

import httpx
import pytest

from scripts.record_scene import load_scene, run_scene
from tests.protocol import validate_event


def scene_file(tmp_path, steps):
    path = tmp_path / "sample.yaml"
    path.write_text(
        "scene: sample\ntitle_zh: 测试场景\ntitle_en: Sample\nsteps:\n" + steps,
        encoding="utf-8",
    )
    return path


def test_load_yaml_and_reject_unknown_step(tmp_path):
    scene = load_scene(scene_file(tmp_path, "  - chat: {lane: customer, text: 你好}\n  - wait_done: {}\n"))
    assert scene["scene"] == "sample"
    assert scene["steps"][0]["chat"]["text"] == "你好"
    with pytest.raises(ValueError, match="unknown step.*teleport"):
        load_scene(scene_file(tmp_path, "  - teleport: {}\n"))


@pytest.mark.anyio
async def test_three_step_recording(tmp_path):
    scene = load_scene(scene_file(tmp_path, "  - chat: {lane: customer, text: 你好}\n  - wait_done: {}\n  - snapshot: {lane: ops, path: /api/review-queue}\n"))
    events = [
        ("session", {"session_id": "42"}),
        ("token", {"text": "您好"}),
        ("done", {"finish_reason": "stop", "message_id": 7}),
    ]
    requests = []

    def handle(request):
        requests.append(request)
        if request.url.path == "/chat/stream":
            assert json.loads(request.content) == {"user_id": "demo-test", "message": "你好", "debug": True}
            body = ": keepalive\n\n" + "".join(
                f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
                for event, data in events
            )
            return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})
        assert request.url.path == "/api/review-queue"
        assert request.url.params["debug"] == "true"
        return httpx.Response(200, json=[])

    output = tmp_path / "sample.jsonl"
    async with httpx.AsyncClient(base_url="http://test", transport=httpx.MockTransport(handle), trust_env=False) as client:
        await run_scene(scene, output, client=client, user_id="demo-test", git_commit="abc123", model="test-model")
    rows = [json.loads(line) for line in output.read_text().splitlines()]
    assert set(rows[0]) == {"scene", "title_zh", "title_en", "recorded_at", "git_commit", "model"}
    assert rows[0]["scene"] == "sample"
    assert rows[0]["model"] == "test-model"
    assert rows[0]["git_commit"] == "abc123"
    assert len(requests) == 2
    assert all(set(row) == {"t_ms", "lane", "channel", "event", "data"} for row in rows[1:])
    times = [row["t_ms"] for row in rows[1:]]
    assert times == sorted(times) and all(t >= 0 for t in times)
    assert rows[-1]["lane"] == "ops"
    assert rows[-1]["data"] == {"path": "/api/review-queue", "response": []}
    recorded = [row for row in rows[1:] if row["channel"] == "sse"]
    assert [(row["event"], row["data"]) for row in recorded] == events
    for row in recorded:
        validate_event(row["event"], row["data"])


@pytest.mark.anyio
async def test_incomplete_stream_preserves_previous_recording(tmp_path):
    scene = load_scene(scene_file(tmp_path, "  - chat: {lane: customer, text: 你好}\n"))
    output = tmp_path / "sample.jsonl"
    output.write_text("previous recording")
    transport = httpx.MockTransport(lambda _: httpx.Response(
        200, text='event: session\ndata: {"session_id": "42"}\n\n'))
    async with httpx.AsyncClient(base_url="http://test", transport=transport, trust_env=False) as client:
        with pytest.raises(RuntimeError, match="did not complete"):
            await run_scene(scene, output, client=client, user_id="demo-test", git_commit="abc", model="test")
    assert output.read_text() == "previous recording"
    assert output.with_suffix(".jsonl.partial").exists()


@pytest.mark.anyio
async def test_inject_restored_on_stream_failure(tmp_path):
    scene = load_scene(scene_file(tmp_path, "  - inject: {server: logistics, delay_seconds: 10}\n  - chat: {lane: customer, text: 你好}\n"))

    class Injector:
        changed = set()
        calls = []

        async def restart(self, server, delay):
            self.changed.add(server)
            self.calls.append((server, delay))

        async def restore(self):
            self.calls.append(("logistics", 0))

    injector = Injector()
    transport = httpx.MockTransport(lambda _: httpx.Response(503))
    async with httpx.AsyncClient(base_url="http://test", transport=transport, trust_env=False) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await run_scene(scene, tmp_path / "sample.jsonl", client=client, user_id="demo-test",
                            git_commit="abc", model="test", injector=injector)
    assert injector.calls == [("logistics", 10), ("logistics", 0)]


def test_all_six_declared_scenes_load():
    from pathlib import Path
    scene_dir = Path(__file__).resolve().parents[1] / "scripts/scenes"
    for name in ("flywheel", "refund", "multi-turn", "ticket", "mcp-timeout", "boundaries"):
        assert load_scene(scene_dir / f"{name}.yaml")["scene"] == name


def test_refund_scene_requests_submission_after_order_resume():
    from pathlib import Path
    scene = load_scene(Path(__file__).resolve().parents[1] / "scripts/scenes/refund.yaml")
    steps = scene["steps"]
    pick = next(i for i, step in enumerate(steps) if "pick_order" in step)
    assert steps[pick + 1] == {"wait_done": {}}
    assert steps[pick + 2] == {"chat": {"lane": "customer", "text": "请帮我提交退款单，七天无理由"}}
    assert steps[pick + 3] == {"wait_done": {"refund_if_offered": True, "reason": "七天无理由"}}


@pytest.mark.anyio
@pytest.mark.parametrize("offered", [False, True])
async def test_followup_turn_conditional_refund_records_response(tmp_path, offered):
    scene = load_scene(scene_file(tmp_path,
        "  - chat: {lane: customer, text: 我想退耳机}\n"
        "  - pick_order: {index: 1}\n"
        "  - wait_done: {}\n"
        "  - chat: {lane: customer, text: 请帮我提交退款单，七天无理由}\n"
        "  - wait_done: {refund_if_offered: true, reason: 七天无理由}\n"))
    paths = []

    def sse(events):
        return httpx.Response(200, text="".join(
            f"event: {n}\ndata: {json.dumps(d)}\n\n" for n, d in events))

    def handle(request):
        paths.append(request.url.path)
        body = json.loads(request.content)
        if len(paths) == 1:
            assert "session_id" not in body
            order = {"order_id": "1001", "title": "耳机", "total": 299,
                     "created_at": "2026-10-04", "status": "已签收"}
            return sse([("session", {"session_id": "42"}), ("order_picker", {"orders": [order]}),
                        ("done", {"finish_reason": "interrupted"})])
        assert body["session_id"] == "42" and body["user_id"] == "demo"
        if request.url.path == "/chat/resume":
            assert body["order_id"] == "1001"
            # A previous turn's button must not trigger submission in the followup.
            return sse([("actions", {"options": [{"type": "refund", "order_id": "1001"}]}),
                        ("done", {"finish_reason": "stop", "message_id": 7})])
        if request.url.path == "/chat/stream":
            assert body["message"] == "请帮我提交退款单，七天无理由"
            events = []
            if offered:
                events.append(("actions", {"options": [{"type": "refund", "order_id": "1001"}]}))
            return sse([*events, ("done", {"finish_reason": "stop", "message_id": 8})])
        assert request.url.path == "/refunds"
        assert body == {"user_id": "demo", "session_id": "42", "order_id": "1001",
                        "reason": "七天无理由", "debug": True}
        return httpx.Response(200, json={"refund_no": "R123", "status": "待审核"})

    output = tmp_path / "sample.jsonl"
    async with httpx.AsyncClient(base_url="http://test", transport=httpx.MockTransport(handle), trust_env=False) as client:
        await run_scene(scene, output, client=client, user_id="demo", git_commit="abc", model="test")
    assert paths == ["/chat/stream", "/chat/resume", "/chat/stream"] + (["/refunds"] if offered else [])
    rows = [json.loads(line) for line in output.read_text().splitlines()[1:]]
    refunds = [row for row in rows if row["event"] == "refund"]
    assert [row["data"] for row in refunds] == ([{
        "path": "/refunds", "response": {"refund_no": "R123", "status": "待审核"},
    }] if offered else [])
    assert all(row["channel"] == "api" and row["lane"] == "customer" for row in refunds)
    for row in rows:
        if row["channel"] == "sse":
            validate_event(row["event"], row["data"])


@pytest.mark.anyio
@pytest.mark.parametrize("offered", [False, True])
async def test_first_order_resume_and_conditional_refund(tmp_path, offered):
    scene = load_scene(scene_file(tmp_path,
        "  - chat: {lane: customer, text: 我想退耳机}\n"
        "  - pick_order: {index: 1}\n"
        "  - wait_done: {refund_if_offered: true}\n"))
    paths = []

    def sse(events):
        return httpx.Response(200, text="".join(
            f"event: {n}\ndata: {json.dumps(d)}\n\n" for n, d in events))

    def handle(request):
        paths.append(request.url.path)
        body = json.loads(request.content)
        assert body["debug"] is True
        if request.url.path == "/chat/stream":
            order = {"order_id": "1001", "title": "耳机", "total": 299,
                     "created_at": "2026-10-04", "status": "已签收"}
            return sse([("session", {"session_id": "42"}), ("order_picker", {"orders": [order]}),
                        ("done", {"finish_reason": "interrupted"})])
        if request.url.path == "/chat/resume":
            assert body["order_id"] == "1001" and body["session_id"] == "42"
            events = [("session", {"session_id": "42"})]
            if offered:
                events.append(("actions", {"options": [{"type": "refund", "order_id": "1001"}]}))
            return sse([*events, ("done", {"finish_reason": "stop", "message_id": 7})])
        assert request.url.path == "/refunds"
        assert body["order_id"] == "1001" and body["reason"] == "七天无理由"
        return httpx.Response(200, json={"refund_no": "R123", "status": "待审核"})

    output = tmp_path / "sample.jsonl"
    async with httpx.AsyncClient(base_url="http://test", transport=httpx.MockTransport(handle), trust_env=False) as client:
        await run_scene(scene, output, client=client, user_id="demo", git_commit="abc", model="test")
    assert paths == ["/chat/stream", "/chat/resume"] + (["/refunds"] if offered else [])
    for row in map(json.loads, output.read_text().splitlines()[1:]):
        if row["channel"] == "sse":
            validate_event(row["event"], row["data"])


@pytest.mark.anyio
async def test_snapshot_export_matches_replay_paths(tmp_path):
    from scripts.export_snapshots import export_snapshots
    replay_dir = tmp_path / "replays"
    replay_dir.mkdir()
    rows = [{"scene": "sample"},
            {"channel": "api", "event": "chat", "data": {"user_id": "demo"}},
            {"channel": "sse", "event": "citations", "data": {"items": [{"chunk_id": 5}]}}]
    (replay_dir / "sample.jsonl").write_text("\n".join(map(json.dumps, rows)))

    responses = {
        "/api/review-queue": [{"id": 3}], "/api/review-queue/3": {"id": 3, "sources": []},
        "/api/eval-runs": [], "/api/faith-cases": [], "/api/tool-audit": [],
        "/api/conversations": [{"session_id": "42", "updated_at": "2026-10-09"}],
        "/api/conversations/42/messages": [{"role": "user", "content": "你好"}],
        "/api/knowledge/chunks/5": {"id": 5, "answer": "真实答案", "prev_chunk_id": 4},
        "/api/knowledge/chunks/4": {"id": 4, "answer": "上一段", "next_chunk_id": 5},
    }

    def handle(request):
        assert request.url.params["debug"] == "true"
        if request.url.path == "/api/tool-audit":
            assert request.url.params["limit"] == "500"
        if request.url.path.startswith("/api/conversations"):
            assert request.url.params["user_id"] == "demo"
        return httpx.Response(200, json=responses[request.url.path])

    output = tmp_path / "snapshots"
    async with httpx.AsyncClient(base_url="http://test", transport=httpx.MockTransport(handle), trust_env=False) as client:
        await export_snapshots(client, output, replay_dir)
    expected = {"review-queue.json", "review-queue/3.json", "eval-runs.json", "faith-cases.json",
                "tool-audit.json", "conversations.json", "conversations/42/messages.json",
                "knowledge/chunks/5.json", "knowledge/chunks/4.json"}
    assert {p.relative_to(output).as_posix() for p in output.rglob("*.json")} == expected
