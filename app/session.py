import asyncio
import uuid
from dataclasses import dataclass, field

from langchain_core.messages import BaseMessage


@dataclass
class Session:
    id: str
    messages: list[BaseMessage] = field(default_factory=list)
    # 同一会话同一时刻只处理一个请求。
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class SessionStore:
    """进程内会话存储。进程重启后清空。"""

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}

    def get(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)

    def get_or_create(self, session_id: str | None) -> Session:
        # session_id 为 None 时生成 UUID4；ID 不存在时新建。
        if session_id is None:
            session_id = str(uuid.uuid4())
        session = self.get(session_id)
        if session is None:
            session = Session(id=session_id)
            self._sessions[session_id] = session
        return session


_store = SessionStore()


def get_session_store() -> SessionStore:
    return _store
