from datetime import date

import pytest
from httpx import ASGITransport, AsyncClient
from langchain_core.callbacks import AsyncCallbackHandler

from app.api.chat import get_today
from app.llm import get_chat_model
from app.main import app
from app.session import SessionStore, get_session_store


@pytest.fixture
def anyio_backend():
    # 只在 asyncio 上运行异步测试。
    return "asyncio"


class RecordingHandler(AsyncCallbackHandler):
    """记录每次模型调用收到的消息列表。"""

    def __init__(self):
        self.calls = []

    async def on_chat_model_start(self, serialized, messages, **kwargs):
        self.calls.append(messages[0])


@pytest.fixture
def store():
    s = SessionStore()
    app.dependency_overrides[get_session_store] = lambda: s
    app.dependency_overrides[get_today] = lambda: date(2026, 10, 6)
    yield s
    app.dependency_overrides.clear()


@pytest.fixture
def use_model(store):
    """用法：use_model(fake_model) 返回 RecordingHandler。"""

    def _use(model):
        rec = RecordingHandler()
        bound = model.with_config(callbacks=[rec])
        app.dependency_overrides[get_chat_model] = lambda: bound
        return rec

    return _use


@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
