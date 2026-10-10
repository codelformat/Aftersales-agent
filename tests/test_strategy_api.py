import json

import pytest

pytestmark = pytest.mark.anyio


async def test_strategy_missing_and_exported_snapshot(client, monkeypatch, tmp_path):
    from app.api import strategy
    path = tmp_path / "strategy-comparison.json"
    monkeypatch.setattr(strategy, "SNAPSHOT_PATH", path)
    response = await client.get("/api/strategy-comparison")
    assert response.status_code == 404
    assert response.json() == {"code": "not_exported"}
    data = {"generated_from": {"report": "real.md"}, "metrics": {"dense": {"R@1": 0.5}}, "cases": []}
    path.write_text(json.dumps(data))
    response = await client.get("/api/strategy-comparison")
    assert response.status_code == 200 and response.json() == data
