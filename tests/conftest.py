from datetime import date
import logging

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.chat import get_today
from app.llm import get_chat_model
from app.main import app
from app.locks import LockRegistry, get_lock_registry
from tests.fakes import Recorder, ScriptedChatModel


@pytest.fixture
def anyio_backend():
    # 只在 asyncio 上运行异步测试。
    return "asyncio"


@pytest.fixture
def locks():
    reg = LockRegistry()
    app.dependency_overrides[get_lock_registry] = lambda: reg
    app.dependency_overrides[get_today] = lambda: date(2026, 10, 6)
    yield reg
    app.dependency_overrides.clear()


@pytest.fixture
def use_script(locks):
    """用法：rec = use_script([第1次调用的chunks], [第2次调用的chunks], ...)。"""

    def _use(*scripts):
        rec = Recorder()
        model = ScriptedChatModel(scripts=list(scripts), recorder=rec)
        app.dependency_overrides[get_chat_model] = lambda: model
        return rec

    return _use


@pytest.fixture
async def client(memory_graph):
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
    """去掉 -- 注释行，只按引号外的分号切分语句。"""
    lines = [line for line in sql.splitlines() if not line.strip().startswith("--")]
    body = "\n".join(lines)
    statements = []
    quote = None
    escaped = False
    start = 0
    for i, char in enumerate(body):
        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
        elif char in "'\"`":
            quote = char
        elif char == ";":
            if statement := body[start:i].strip():
                statements.append(statement)
            start = i + 1
    if statement := body[start:].strip():
        statements.append(statement)
    return statements


async def _reset_schema(url: str) -> None:
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            for table in (
                "faith_cases", "low_confidence_questions", "qa_extraction_staging", "knowledge_chunks",
                "messages", "tickets", "conversations", "faq",
            ):
                await conn.exec_driver_sql(f"DROP TABLE IF EXISTS {table}")
            for name in ("schema.sql", "schema_ch03.sql", "schema_ch04.sql", "seed.sql"):
                for stmt in split_sql((ROOT / "db" / name).read_text(encoding="utf-8")):
                    await conn.exec_driver_sql(stmt)
    finally:
        await engine.dispose()


async def _clear_runtime_tables(url: str) -> None:
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            await conn.exec_driver_sql(
                "UPDATE knowledge_chunks SET prev_chunk_id = NULL, next_chunk_id = NULL"
            )
            for table in (
                "faith_cases", "low_confidence_questions", "qa_extraction_staging", "knowledge_chunks",
                "messages", "tickets", "conversations",
            ):
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


from pymilvus import AsyncMilvusClient

from app.config import KNOWLEDGE_TEST_COLLECTION
from app.knowledge import milvus as milvus_mod
import app.knowledge.rerank as rerank_mod
from app.knowledge.embeddings import set_embeddings
from tests.fakes import FakeEmbeddings

logger = logging.getLogger(__name__)


class BlockedMilvus:
    """没有使用 fixture milvus 的测试，访问 Milvus 时立即失败。"""

    def __getattr__(self, name):
        raise RuntimeError("测试未启用 fixture milvus")


class BlockedReranker:
    """测试访问真实重排接口时立即失败。"""

    def __getattr__(self, name):
        raise RuntimeError("测试未替换重排客户端")


@pytest.fixture(autouse=True)
def _isolate_knowledge():
    set_embeddings(FakeEmbeddings())
    milvus_mod.set_milvus(BlockedMilvus(), KNOWLEDGE_TEST_COLLECTION)
    rerank_mod.set_rerank_client(BlockedReranker())
    yield
    set_embeddings(None)
    milvus_mod.set_milvus(None)
    rerank_mod.set_rerank_client(None)


@pytest.fixture
async def milvus():
    """每个测试重建 Milvus 测试集合。连不上时直接失败，不跳过。"""
    client = AsyncMilvusClient(uri=get_settings().milvus_uri, timeout=5)
    try:
        try:
            if await client.has_collection(KNOWLEDGE_TEST_COLLECTION, timeout=5):
                await client.drop_collection(KNOWLEDGE_TEST_COLLECTION, timeout=5)
        except Exception:
            logger.exception("无法连接 Milvus")
            pytest.fail("无法连接 Milvus，请先执行 docker compose up -d --wait", pytrace=False)
        milvus_mod.set_milvus(client, KNOWLEDGE_TEST_COLLECTION)
        await milvus_mod.ensure_collection()
        yield client
    finally:
        milvus_mod.set_milvus(BlockedMilvus(), KNOWLEDGE_TEST_COLLECTION)
        await client.close()


def _blocked_factory(name: str):
    def factory():
        raise RuntimeError(f"测试未替换 {name}")
    return factory


@pytest.fixture(autouse=True)
def _block_llm_runnables(monkeypatch):
    """测试不调用上游模型。需要时在测试中传入 RunnableLambda。"""
    from app.knowledge import query
    monkeypatch.setattr(query, "get_query_rewriter", _blocked_factory("get_query_rewriter"))

    from app.services import grounding
    monkeypatch.setattr(grounding, "get_self_checker", _blocked_factory("get_self_checker"))

    from app.services import understanding
    monkeypatch.setattr(understanding, "get_query_expander", _blocked_factory("get_query_expander"))

    from evals import run_faith_judge_eval, run_rag_eval
    monkeypatch.setattr(run_rag_eval, "get_chat_model", _blocked_factory("get_chat_model"))
    for module in (run_rag_eval, run_faith_judge_eval):
        monkeypatch.setattr(module, "get_faith_judge", _blocked_factory("get_faith_judge"))


@pytest.fixture(autouse=True)
def _block_intent_classifier(monkeypatch):
    from app.services import understanding
    monkeypatch.setattr(understanding, "get_intent_classifier", _blocked_factory("get_intent_classifier"))
    monkeypatch.setattr(understanding, "get_small_intent_classifier", _blocked_factory("get_small_intent_classifier"))


def _intent_fixture(monkeypatch, attr):
    from langchain_core.runnables import RunnableLambda
    from app.services import understanding
    from app.schemas import IntentResult

    def _use(*values):
        queue = list(values)
        calls = []

        async def classify(inputs):
            calls.append(inputs)
            value = queue.pop(0)
            if isinstance(value, BaseException):
                raise value
            if value is None:
                return {"parsed": None, "raw": None}
            intent, confidence = value if isinstance(value, tuple) else (value, 0.9)
            return {"parsed": IntentResult(intent=intent, confidence=confidence), "raw": None}

        monkeypatch.setattr(understanding, attr, lambda: RunnableLambda(classify))
        return calls

    return _use


@pytest.fixture
def use_intent(monkeypatch):
    """用法：use_intent("物流", ("其他", 0.4))。每次识别消费一个值；None 表示解析失败；异常实例表示抛出。"""
    return _intent_fixture(monkeypatch, "get_intent_classifier")


@pytest.fixture
def use_small_intent(monkeypatch):
    return _intent_fixture(monkeypatch, "get_small_intent_classifier")


def _resolver_runnable(values, calls):
    from langchain_core.runnables import RunnableLambda
    from app.schemas import ResolvedQuery

    async def resolve(inputs):
        calls.append(inputs)
        value = values.pop(0) if values is not None else {}
        if isinstance(value, BaseException):
            raise value
        if value is None:
            return {"parsed": None, "raw": None}
        fields = {"resolved_input": inputs["question"], "standard_query": inputs["question"], **value}
        return {"parsed": ResolvedQuery(**fields), "raw": None}

    return RunnableLambda(resolve)


@pytest.fixture(autouse=True)
def _default_resolver(monkeypatch):
    """默认透传，等价于 ch05 的 resolve_reference。需要时用 use_resolver 覆盖。"""
    from app.services import understanding
    monkeypatch.setattr(understanding, "get_reference_resolver", lambda: _resolver_runnable(None, []))


@pytest.fixture
def use_resolver(monkeypatch):
    """用法：use_resolver({"resolved_input": ..., "order_id": ...}, None, ValueError())。"""
    from app.services import understanding

    def _use(*values):
        queue, calls = list(values), []
        monkeypatch.setattr(understanding, "get_reference_resolver", lambda: _resolver_runnable(queue, calls))
        return calls

    return _use


@pytest.fixture
def use_expander(monkeypatch):
    """用法：use_expander(["查询1", "查询2"], None, ValueError())。"""
    from langchain_core.runnables import RunnableLambda
    from app.schemas import QueryExpansion
    from app.services import understanding

    def _use(*values):
        queue, calls = list(values), []

        async def expand(inputs):
            calls.append(inputs)
            value = queue.pop(0)
            if isinstance(value, BaseException):
                raise value
            # 绕过条数校验，测试代码侧的截断。
            return {"parsed": None if value is None else QueryExpansion.model_construct(queries=value), "raw": None}

        monkeypatch.setattr(understanding, "get_query_expander", lambda: RunnableLambda(expand))
        return calls

    return _use


@pytest.fixture
def emitted(monkeypatch):
    """直接调用节点时，收集节点发出的事件。"""
    from app.graph import events
    out = []
    monkeypatch.setattr(events, "get_stream_writer", lambda: out.append)
    return out


@pytest.fixture
def memory_graph():
    from langgraph.checkpoint.memory import InMemorySaver
    from app.graph.builder import build_graph, set_graph
    graph = build_graph(InMemorySaver())
    set_graph(graph)
    yield graph
    set_graph(None)
