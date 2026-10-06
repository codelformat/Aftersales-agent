import json
from datetime import date, datetime, time, timedelta
from pathlib import Path

from sqlalchemy import select

from app.db.engine import get_sessionmaker
from app.db.models import Conversation, Message
from app.repositories.staging import db_utc_offset, local_to_db

HISTORY_FILE = Path(__file__).resolve().parents[2] / "knowledge" / "history" / "conversations.jsonl"
HISTORY_USER_ID = "history-seed"


async def seed_history(day: date, path: Path = HISTORY_FILE) -> int:
    """按本地日期写入样例历史对话，时间转为数据库时区。该日期已有种子会话时返回 0。"""
    start = datetime.combine(day, time(9, 0))
    async with get_sessionmaker()() as s:
        offset = await db_utc_offset(s)
        existing = await s.scalar(select(Conversation.id).where(
            Conversation.user_id == HISTORY_USER_ID,
            Conversation.created_at >= local_to_db(datetime.combine(day, time.min), offset),
            Conversation.created_at < local_to_db(datetime.combine(day + timedelta(days=1), time.min), offset),
        ).limit(1))
        if existing is not None:
            return 0
        lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        for i, item in enumerate(lines):
            at = start + timedelta(minutes=10 * i)
            db_at = local_to_db(at, offset)
            conv = Conversation(user_id=HISTORY_USER_ID, created_at=db_at, updated_at=db_at)
            s.add(conv)
            await s.flush()
            for j, msg in enumerate(item["messages"]):
                s.add(Message(
                    conversation_id=conv.id, role=msg["role"], content=msg["content"],
                    created_at=local_to_db(at + timedelta(seconds=j), offset),
                ))
        await s.commit()
        return len(lines)
