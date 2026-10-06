import json
from datetime import date, datetime, time, timedelta
from pathlib import Path

from sqlalchemy import select

from app.db.engine import get_sessionmaker
from app.db.models import Conversation, Message

HISTORY_FILE = Path(__file__).resolve().parents[2] / "knowledge" / "history" / "conversations.jsonl"
HISTORY_USER_ID = "history-seed"


async def seed_history(day: date, path: Path = HISTORY_FILE) -> int:
    """把样例历史对话写入 conversations 和 messages，created_at 设为 day。该日期已有种子会话时返回 0。"""
    start = datetime.combine(day, time(9, 0))
    async with get_sessionmaker()() as s:
        existing = await s.scalar(select(Conversation.id).where(
            Conversation.user_id == HISTORY_USER_ID,
            Conversation.created_at >= datetime.combine(day, time.min),
            Conversation.created_at < datetime.combine(day, time.min) + timedelta(days=1),
        ).limit(1))
        if existing is not None:
            return 0
        lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        for i, item in enumerate(lines):
            at = start + timedelta(minutes=10 * i)
            conv = Conversation(user_id=HISTORY_USER_ID, created_at=at, updated_at=at)
            s.add(conv)
            await s.flush()
            for j, msg in enumerate(item["messages"]):
                s.add(Message(
                    conversation_id=conv.id, role=msg["role"], content=msg["content"],
                    created_at=at + timedelta(seconds=j),
                ))
        await s.commit()
        return len(lines)
