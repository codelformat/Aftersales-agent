"""ch07 验收用的对话客户端：按顺序发送，每轮读完 SSE 再发下一轮。"""

import argparse
import asyncio
import json
import sys

import httpx


async def ask(client: httpx.AsyncClient, base: str, user: str, message: str, session: str | None) -> dict:
    body = {"user_id": user, "message": message, **({"session_id": session} if session else {})}
    events, name = [], None
    async with client.stream("POST", f"{base}/chat/stream", json=body) as r:
        r.raise_for_status()
        async for line in r.aiter_lines():
            if line.startswith("event: "):
                name = line[7:]
            elif line.startswith("data: "):
                events.append((name, json.loads(line[6:])))
    sid = next((d["session_id"] for n, d in events if n == "session"), session)
    reply = "".join(d["text"] for n, d in events if n == "token")
    finish = events[-1][1].get("finish_reason") if events and events[-1][0] == "done" else None
    return {"session_id": sid, "reply": reply, "finish": finish}


async def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--base", default="http://127.0.0.1:8000")
    p.add_argument("--user", required=True)
    p.add_argument("--dialog")
    p.add_argument("--session")
    p.add_argument("--ask")
    a = p.parse_args()
    lines = json.loads(open(a.dialog, encoding="utf-8").read()) if a.dialog else []
    if a.ask:
        lines.append(a.ask)
    session, errors, last = a.session, 0, ""
    async with httpx.AsyncClient(timeout=180) as client:
        for i, message in enumerate(lines, 1):
            r = await ask(client, a.base, a.user, message, session)
            session, last = r["session_id"], r["reply"]
            errors += r["finish"] != "stop"
            print(f"turn={i} session={session} finish={r['finish']} reply={last[:80]}")
    print(json.dumps({"session_id": session, "turns": len(lines), "errors": errors, "last_reply": last},
                     ensure_ascii=False))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
