import pytest

from evals import run_chat_samples as samples
from app.graph.builder import get_graph, open_graph

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("fail_load", [False, True])
async def test_run_samples_closes_resources_in_order(monkeypatch, tmp_path, fail_load):
    calls = []

    async def close():
        calls.append("milvus")

    async def dispose():
        calls.append("engine")

    def load():
        if fail_load:
            raise ValueError("样例读取失败")
        return []

    monkeypatch.setattr(samples, "close_milvus", close, raising=False)
    monkeypatch.setattr(samples, "dispose_engine", dispose, raising=False)
    monkeypatch.setattr(samples, "load_samples", load)
    monkeypatch.setattr(samples, "open_graph", lambda: open_graph(str(tmp_path / "cp.sqlite")), raising=False)
    if fail_load:
        with pytest.raises(ValueError, match="样例读取失败"):
            await samples.run_samples()
    else:
        assert await samples.run_samples() == 0
    assert calls == ["milvus", "engine"]


async def test_run_samples_opens_graph_and_prints_actions(db, use_script, use_intent, tmp_path, monkeypatch, capsys):
    path = tmp_path / "cp.sqlite"
    use_intent("投诉")
    use_script()
    monkeypatch.setattr(samples, "load_samples", lambda: [("投诉", "我要投诉")])
    monkeypatch.setattr(samples, "open_graph", lambda: open_graph(str(path)), raising=False)

    async def close():
        pass

    monkeypatch.setattr(samples, "close_milvus", close)
    assert await samples.run_samples() == 0
    assert "人工选项：['handoff', 'ticket']" in capsys.readouterr().out
    assert path.exists()
    with pytest.raises(RuntimeError, match="图未初始化"):
        get_graph()
