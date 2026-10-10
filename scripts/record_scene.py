"""Record a YAML scene against the local system.

Usage: uv run python scripts/record_scene.py flywheel
For inject, pass --server-pid-dir with PID files for servers started by this run.
The recorder replaces that server, updates its PID file, and restores delay=0
in finally. The caller must stop the final PID when the recording run ends.
The caller must also reap its original server processes during replacement.
pick_order indices start at 1. chat.new_session starts a fresh conversation.
wait_done.refund_if_offered submits the displayed form with the declared reason.
"""

import argparse
import asyncio
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from uuid import uuid4

import httpx
import yaml
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.retry import retry_async

STEP_FIELDS = {
    "chat": {"lane": str, "text": str}, "wait_done": {},
    "pick_order": {"index": int}, "confirm_ticket": {"confirmed": bool},
    "feedback": {"rating": str}, "poll_review": {"contains": str, "timeout_s": (int, float)},
    "approve": {"answer": str, "category": str}, "snapshot": {"lane": str, "path": str},
    "inject": {"server": str, "delay_seconds": (int, float)},
    "reset_demo_chunk": {"contains": str},
}


def load_scene(path: Path) -> dict:
    scene = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(scene, dict) or any(not isinstance(scene.get(k), str) or not scene[k].strip()
                                         for k in ("scene", "title_zh", "title_en")):
        raise ValueError("scene, title_zh and title_en must be nonempty strings")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", scene["scene"]):
        raise ValueError("invalid scene id")
    if not isinstance(scene.get("steps"), list) or not scene["steps"]:
        raise ValueError("steps must be a nonempty list")
    for step in scene["steps"]:
        if not isinstance(step, dict) or len(step) != 1:
            raise ValueError("each step must contain one operation")
        name, params = next(iter(step.items()))
        if name not in STEP_FIELDS:
            raise ValueError(f"unknown step: {name}")
        if params is None:
            step[name] = params = {}
        if not isinstance(params, dict):
            raise ValueError(f"{name}: parameters must be a mapping")
        for key, expected in STEP_FIELDS[name].items():
            value = params.get(key)
            if not isinstance(value, expected) or (expected != bool and isinstance(value, bool)):
                raise ValueError(f"{name}.{key}: invalid or missing value")
            if isinstance(value, str) and not value.strip():
                raise ValueError(f"{name}.{key}: empty value")
        if params.get("lane", "customer") not in ("customer", "ops"):
            raise ValueError(f"{name}: invalid lane")
        if name == "pick_order" and params["index"] < 1:
            raise ValueError("pick_order.index must start at 1")
        if name == "feedback" and params["rating"] not in ("up", "down"):
            raise ValueError("feedback.rating must be up or down")
        if name == "poll_review" and params["timeout_s"] <= 0:
            raise ValueError("poll_review.timeout_s must be positive")
        if name == "inject" and (params["server"] not in ("logistics", "aftersales") or
                                 params["delay_seconds"] < 0):
            raise ValueError("inject: invalid server or delay")
        if name == "snapshot" and not params["path"].startswith("/api/"):
            raise ValueError("snapshot.path must be a local API path")
    return scene


async def reset_demo_chunk(contains: str) -> dict:
    from sqlalchemy import select
    from app.db.engine import dispose_engine, get_sessionmaker
    from app.db.models import KnowledgeChunk
    from app.knowledge.milvus import close_milvus, delete_vectors
    from app.repositories.knowledge import delete_ids

    try:
        async with get_sessionmaker()() as session:
            ids = list(await session.scalars(select(KnowledgeChunk.id).where(
                KnowledgeChunk.content_type == "flywheel", KnowledgeChunk.questions.contains(contains))))
            await delete_vectors(ids)
            await delete_ids(session, ids)
            await session.commit()
        return {"contains": contains, "deleted_ids": ids}
    finally:
        try:
            await close_milvus()
        finally:
            await dispose_engine()


def port_open(port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.2)
        return sock.connect_ex(("127.0.0.1", port)) == 0


class ServerInjector:
    def __init__(self, pid_dir: Path):
        self.pid_dir = pid_dir
        self.changed = set()
        self.children = {}

    async def restart(self, server: str, delay: float):
        port = {"logistics": 8101, "aftersales": 8102}[server]
        pid_file = self.pid_dir / f"{server}.pid"
        pid = int(pid_file.read_text().strip())
        if pid <= 1:
            raise ValueError("invalid owned server PID")
        # Only use the PID directory supplied by the caller for this run.
        # The caller must reap its original child during replacement.
        self.changed.add(server)
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass

        async def stopped():
            child = self.children.get(server)
            if child is not None:
                child.poll()
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                pass
            else:
                raise RuntimeError(f"{server} process still running")
            if port_open(port):
                raise RuntimeError(f"port {port} still occupied")

        try:
            await retry_async(stopped, attempts=12, base_delay=0.2, max_delay=1, retry_on=(RuntimeError,))
        except RuntimeError:
            os.kill(pid, signal.SIGKILL)
            await retry_async(stopped, attempts=8, base_delay=0.2, max_delay=1, retry_on=(RuntimeError,))
        env = {**os.environ, "MOCK_DELAY_SECONDS": str(delay)}
        with (self.pid_dir / f"{server}.out").open("ab") as log:
            child = subprocess.Popen([sys.executable, "-m", f"mcp_servers.{server}", "--port", str(port)],
                                     cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
        self.children[server] = child
        pid_file.write_text(str(child.pid))

        async def ready():
            if child.poll() is not None:
                raise RuntimeError(f"{server} exited during startup")
            if not port_open(port):
                raise RuntimeError(f"{server} not ready")

        await retry_async(ready, attempts=25, base_delay=0.2, max_delay=1, retry_on=(RuntimeError,))

    async def restore(self):
        # Try all changed servers even if one restore fails.
        failures = []
        for server in sorted(self.changed):
            try:
                await self.restart(server, 0)
            except Exception as exc:
                failures.append(exc)
        if failures:
            raise ExceptionGroup("MCP restore failed", failures)


class Recorder:
    def __init__(self, client, stream, user_id, injector, recorded_at):
        self.client, self.stream, self.user_id, self.injector = client, stream, user_id, injector
        self.started = time.monotonic()
        self.recorded_at = recorded_at
        self.session_id = None
        self.last = []
        self.review_id = None
        self.feedback_id = None
        self.validator = Draft202012Validator(json.loads((ROOT / "web/src/protocol/events.schema.json").read_text()))

    def write(self, lane, channel, event, data):
        if channel == "sse":
            self.validator.validate({"event": event, "data": data})
        row = {"t_ms": int((time.monotonic() - self.started) * 1000), "lane": lane,
               "channel": channel, "event": event, "data": data}
        self.stream.write(json.dumps(row, ensure_ascii=False) + "\n")
        self.stream.flush()

    async def api(self, method, path, lane="ops", event="snapshot", payload=None, timeout=None):
        kwargs = {"params": [*httpx.URL(path).params.multi_items(), ("debug", "true")]}
        if payload is not None:
            kwargs["json"] = {**payload, "debug": True}
        if timeout is not None:
            kwargs["timeout"] = timeout
        response = await self.client.request(method, path, **kwargs)
        response.raise_for_status()
        data = response.json()
        self.write(lane, "api", event, {"path": path, "response": data})
        return data

    async def sse(self, path, payload, lane, operation):
        payload = {**payload, "debug": True}
        self.write(lane, "api", operation, payload)
        self.last = []
        name, data = None, []

        def flush():
            nonlocal name, data
            if data:
                if not name:
                    raise ValueError("SSE data without event name")
                value = json.loads("\n".join(data))
                self.write(lane, "sse", name, value)
                self.last.append((name, value))
                if name == "session":
                    self.session_id = value["session_id"]
            name, data = None, []

        async with self.client.stream("POST", path, json=payload) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line:
                    flush()
                elif line.startswith("event:"):
                    name = line[6:].lstrip()
                elif line.startswith("data:"):
                    data.append(line[5:].lstrip())
            flush()
        self.wait_done()

    def wait_done(self):
        if not self.last or self.last[-1][0] != "done" or any(n == "error" for n, _ in self.last):
            raise RuntimeError("conversation did not complete with done")

    def last_event(self, name):
        for event, data in reversed(self.last):
            if name == event:
                return data
        raise RuntimeError(f"expected {name} in the last turn")

    def resume_payload(self):
        if self.session_id is None:
            raise RuntimeError("no conversation to resume")
        return {"user_id": self.user_id, "session_id": self.session_id}

    async def poll_review(self, params):
        deadline = time.monotonic() + params["timeout_s"]

        async def poll():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("review queue deadline exceeded")
            async with asyncio.timeout(remaining):
                rows = await self.api("GET", "/api/review-queue?status=待审", event="poll_review",
                                      timeout=min(5, remaining))
                for row in rows:
                    detail = await self.api("GET", f'/api/review-queue/{row["id"]}', event="poll_review",
                                            timeout=min(5, remaining))
                    for source in detail["sources"]:
                        created = datetime.fromisoformat(source["created_at"])
                        if created.tzinfo is None:
                            created = created.replace(tzinfo=timezone.utc)
                        if params["contains"] not in source["raw_question"]:
                            continue
                        if self.feedback_id is not None:
                            if source["id"] != self.feedback_id or source["source"] != "user_feedback":
                                continue
                        elif created < self.recorded_at or not source["retrieved_chunks"]:
                            continue
                        if detail["review_status"] == "待审":
                            self.review_id = detail["id"]
                            return detail
            raise LookupError("no fresh matching review source")

        await retry_async(poll, attempts=100, base_delay=0.5, max_delay=4, retry_on=(LookupError,))

    async def step(self, name, p):
        lane = p.get("lane", "customer")
        if name == "chat":
            if p.get("new_session"):
                self.session_id = None
            payload = {"user_id": self.user_id, "message": p["text"]}
            if self.session_id is not None:
                payload["session_id"] = self.session_id
            await self.sse("/chat/stream", payload, lane, "chat")
        elif name == "wait_done":
            self.wait_done()
            if p.get("refund_if_offered"):
                options = [option for n, d in self.last if n == "actions" for option in d["options"]
                           if option["type"] == "refund"]
                if options:
                    await self.api("POST", "/refunds", lane, "refund", {
                        **self.resume_payload(), "order_id": options[0]["order_id"],
                        "reason": p.get("reason", "七天无理由"),
                    })
        elif name == "pick_order":
            orders = self.last_event("order_picker")["orders"]
            if p["index"] > len(orders):
                raise ValueError("order index outside picker")
            await self.sse("/chat/resume", {**self.resume_payload(), "order_id": orders[p["index"] - 1]["order_id"]},
                           lane, "pick_order")
        elif name == "confirm_ticket":
            self.last_event("ticket_preview")
            await self.sse("/chat/resume", {**self.resume_payload(), "ticket_confirm": p["confirmed"]},
                           lane, "confirm_ticket")
        elif name == "feedback":
            result = await self.api("POST", "/api/feedback", lane, "feedback", {
                "user_id": self.user_id, "conversation_id": int(self.session_id),
                "message_id": self.last_event("done")["message_id"], "rating": p["rating"],
            })
            self.feedback_id = result["id"] if p["rating"] == "down" else None
        elif name == "poll_review":
            await self.poll_review(p)
        elif name == "approve":
            if self.review_id is None:
                raise RuntimeError("approve requires poll_review")
            await self.api("POST", f"/api/review-queue/{self.review_id}/approve", "ops", "approve",
                           {"approved_answer": p["answer"], "product_category": p["category"]})
            await self.api("GET", f"/api/review-queue/{self.review_id}")
        elif name == "snapshot":
            await self.api("GET", p["path"], lane)
        elif name == "reset_demo_chunk":
            self.write("ops", "api", name, await reset_demo_chunk(p["contains"]))
        elif name == "inject":
            if self.injector is None:
                raise ValueError("inject requires --server-pid-dir from this recording run")
            await self.injector.restart(p["server"], p["delay_seconds"])
            self.write("ops", "api", "inject", p)
        else:
            raise ValueError(f"unknown step: {name}")


async def run_scene(scene, output, *, client, user_id, git_commit, model, injector=None):
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_suffix(".jsonl.partial")
    recorded_at = datetime.now(timezone.utc).replace(microsecond=0)
    header = {k: scene[k] for k in ("scene", "title_zh", "title_en")}
    header.update(recorded_at=recorded_at.isoformat(), git_commit=git_commit, model=model)
    with partial.open("w", encoding="utf-8") as stream:
        stream.write(json.dumps(header, ensure_ascii=False) + "\n")
        recorder = Recorder(client, stream, user_id, injector, recorded_at)
        try:
            for step in scene["steps"]:
                name, params = next(iter(step.items()))
                await recorder.step(name, params)
        finally:
            if injector is not None and injector.changed:
                await injector.restore()
                recorder.write("ops", "api", "inject_restore", {"servers": sorted(injector.changed), "delay_seconds": 0})
    partial.replace(output)


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scene")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "web/public/replays")
    parser.add_argument("--server-pid-dir", type=Path)
    parser.add_argument("--user-id", help="Override the scene's deterministic mock-order user")
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", args.scene):
        parser.error("invalid scene id")
    scene = load_scene(ROOT / "scripts/scenes" / f"{args.scene}.yaml")
    from app.config import get_settings
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    injector = ServerInjector(args.server_pid_dir) if args.server_pid_dir else None
    async with httpx.AsyncClient(base_url=args.base_url, trust_env=False, timeout=180) as client:
        await run_scene(scene, args.output_dir / f"{args.scene}.jsonl", client=client,
                        user_id=args.user_id or scene.get("user_id") or f"demo10-{args.scene}-{uuid4().hex[:12]}", git_commit=commit,
                        model=get_settings().chat_model, injector=injector)
    print(f"Recorded {args.scene}")


if __name__ == "__main__":
    asyncio.run(main())
