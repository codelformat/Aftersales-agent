from datetime import datetime

import pytest

from app.repositories import eval_runs

pytestmark = pytest.mark.anyio


async def test_eval_runs_returns_recent_rows_ascending_with_original_metrics(db, client):
    rows = []
    async with db() as s:
        for i, trigger in enumerate(("手动", "定时", "手动")):
            rows.append(await eval_runs.add(
                s, triggered_by=trigger, dataset_size=300 if i < 2 else 5,
                metrics={"mrr": i / 10, "failures": i, "extra": {"说明": [True, None]}},
            ))
        await s.commit()

    response = await client.get("/api/eval-runs", params={"limit": 2})

    assert response.status_code == 200
    got = response.json()
    assert [item["id"] for item in got] == [row.id for row in rows[-2:]]
    for item, row in zip(got, rows[-2:]):
        assert set(item) == {"id", "triggered_by", "dataset_size", "metrics", "created_at"}
        assert item["triggered_by"] == row.triggered_by
        assert item["dataset_size"] == row.dataset_size
        assert item["metrics"] == row.metrics
        assert datetime.fromisoformat(item["created_at"]) == row.created_at
