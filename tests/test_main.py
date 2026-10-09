import importlib
import logging

import pytest

main = importlib.import_module("app.main")
pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def _tmp_log(monkeypatch, tmp_path):
    """日志写到临时文件，测试结束移除新增的 handler。"""
    monkeypatch.setattr(main, "LOG_PATH", tmp_path / "app.log", raising=False)
    root = logging.getLogger()
    before = list(root.handlers)
    try:
        yield
    finally:
        for handler in root.handlers[:]:
            if handler not in before:
                root.removeHandler(handler)
                handler.close()


async def test_lifespan_closes_milvus(monkeypatch, caplog, tmp_path):
    caplog.set_level(logging.INFO)
    calls = []

    async def close():
        calls.append("close")

    monkeypatch.setattr(main, "close_milvus", close)
    async with main.app.router.lifespan_context(main.app):
        assert calls == []
    assert calls == ["close"]
    assert "budget window=" in (tmp_path / "app.log").read_text(encoding="utf-8")


async def test_lifespan_logs_budget(monkeypatch, caplog, tmp_path):
    async def close():
        pass

    monkeypatch.setattr(main, "close_milvus", close)
    caplog.set_level(logging.INFO)
    async with main.app.router.lifespan_context(main.app):
        pass
    assert "budget window=" in caplog.text
    assert "budget window=" in (tmp_path / "app.log").read_text(encoding="utf-8")


def test_measure_system_tokens_is_positive():
    from app.graph.nodes.agent import measure_system_tokens

    assert measure_system_tokens() > 500


def test_setup_file_logging_is_idempotent(caplog, tmp_path):
    caplog.set_level(logging.INFO)
    path = tmp_path / "nested" / "app.log"
    main.setup_file_logging(path)
    main.setup_file_logging(path)
    logging.getLogger(__name__).info("日志只写一次")
    text = path.read_text(encoding="utf-8")
    assert text.count("日志只写一次") == 1
    assert "INFO" in text
