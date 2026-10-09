"""ch08 验收用的对话客户端：发消息或恢复，读完 SSE，事件逐行写入 jsonl，末行打印摘要 JSON。"""

import argparse
import asyncio
import json
import sys

import httpx


async def call(client: httpx.AsyncClient, url: str, body: dict) -> list[tuple[str, dict]]:
    events, name = [], None
    async with client.stream("POST", url, json=body) as r:
        r.raise_for_status()
        async for line in r.aiter_lines():
            if line.startswith("event: "):
                name = line[7:]
            elif line.startswith("data: "):
                events.append((name, json.loads(line[6:])))
    return events


async def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--base", default="http://127.0.0.1:8000")
    p.add_argument("--user", required=True)
    p.add_argument("--session")
    p.add_argument("--message")
    p.add_argument("--confirm", choices=("true", "false"), help="调用 /chat/resume 回答工单确认")
    p.add_argument("--out", required=True, help="事件追加写入的 jsonl 文件")
    a = p.parse_args()
    if bool(a.message) == bool(a.confirm):
        p.error("--message 与 --confirm 二选一")

    # 本机地址不走代理。
    async with httpx.AsyncClient(timeout=180, trust_env=False) as client:
        if a.message:
            body = {"user_id": a.user, "message": a.message, **({"session_id": a.session} if a.session else {})}
            events = await call(client, f"{a.base}/chat/stream", body)
        else:
            body = {"user_id": a.user, "session_id": a.session, "ticket_confirm": a.confirm == "true"}
            events = await call(client, f"{a.base}/chat/resume", body)

    with open(a.out, "a", encoding="utf-8") as f:
        for name, data in events:
            f.write(json.dumps({"event": name, "data": data}, ensure_ascii=False) + "\n")
    names = [n for n, _ in events]
    summary = {
        "session_id": next((d["session_id"] for n, d in events if n == "session"), a.session),
        "reply": "".join(d.get("text", "") for n, d in events if n == "token"),
        "events": names,
        "ticket_preview": "ticket_preview" in names,
        "finish": events[-1][1].get("finish_reason") if events and events[-1][0] == "done" else None,
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if summary["finish"] in ("stop", "interrupted") else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
