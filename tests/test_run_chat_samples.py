import pytest

from evals import run_chat_samples as samples

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("fail_load", [False, True])
async def test_run_samples_closes_resources_in_order(monkeypatch, fail_load):
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
    if fail_load:
        with pytest.raises(ValueError, match="样例读取失败"):
            await samples.run_samples()
    else:
        assert await samples.run_samples() == 0
    assert calls == ["milvus", "engine"]
