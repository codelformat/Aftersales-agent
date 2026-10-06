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


import asyncio
from pathlib import Path

from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import get_settings, test_database_url
from app.db.engine import set_sessionmaker

ROOT = Path(__file__).resolve().parent.parent


def split_sql(sql: str) -> list[str]:
    """去掉 -- 注释行，按分号切分语句。"""
    lines = [line for line in sql.splitlines() if not line.strip().startswith("--")]
    return [s.strip() for s in "\n".join(lines).split(";") if s.strip()]


async def _reset_schema(url: str) -> None:
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            for table in ("messages", "tickets", "conversations", "faq"):
                await conn.exec_driver_sql(f"DROP TABLE IF EXISTS {table}")
            for name in ("schema.sql", "seed.sql"):
                for stmt in split_sql((ROOT / "db" / name).read_text(encoding="utf-8")):
                    await conn.exec_driver_sql(stmt)
    finally:
        await engine.dispose()


async def _clear_runtime_tables(url: str) -> None:
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            for table in ("messages", "tickets", "conversations"):
                await conn.exec_driver_sql(f"DELETE FROM {table}")
    finally:
        await engine.dispose()


@pytest.fixture(scope="session")
def test_db_url():
    return test_database_url(get_settings().database_url)


@pytest.fixture(scope="session")
def _test_schema(test_db_url):
    # 连不上测试库时直接失败，不跳过。
    try:
        asyncio.run(_reset_schema(test_db_url))
    except OperationalError as exc:
        pytest.fail(
            f"无法连接测试库 aftersales_test，请先执行 docker compose up -d --wait（{exc.orig}）",
            pytrace=False,
        )


@pytest.fixture
def db(_test_schema, test_db_url):
    """测试库的 sessionmaker。每个测试前清空运行时表，并设为全局 sessionmaker。"""
    asyncio.run(_clear_runtime_tables(test_db_url))
    engine = create_async_engine(test_db_url, poolclass=NullPool)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    set_sessionmaker(sm)
    yield sm
    set_sessionmaker(None)
    asyncio.run(engine.dispose())
