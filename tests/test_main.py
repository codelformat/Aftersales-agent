import importlib

import pytest

main = importlib.import_module("app.main")
pytestmark = pytest.mark.anyio


async def test_lifespan_closes_milvus(monkeypatch):
    calls = []

    async def close():
        calls.append("close")

    monkeypatch.setattr(main, "close_milvus", close)
    async with main.app.router.lifespan_context(main.app):
        assert calls == []
    assert calls == ["close"]
