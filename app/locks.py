import asyncio


class LockRegistry:
    """按会话编号保存锁。"""

    def __init__(self) -> None:
        self._locks: dict[int, asyncio.Lock] = {}

    def get(self, conversation_id: int) -> asyncio.Lock:
        if conversation_id not in self._locks:
            self._locks[conversation_id] = asyncio.Lock()
        return self._locks[conversation_id]


_registry = LockRegistry()


def get_lock_registry() -> LockRegistry:
    """返回进程内共用的会话锁注册表。"""
    return _registry
