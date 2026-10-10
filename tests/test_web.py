import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api import web

pytestmark = pytest.mark.anyio


async def test_unbuilt_home_has_build_instructions(client, monkeypatch, tmp_path):
    monkeypatch.setattr(web, "DIST_DIR", tmp_path / "missing", raising=False)
    response = await client.get("/")
    assert response.status_code == 200
    assert "npm --prefix web ci &amp;&amp; npm --prefix web run build" in response.text


async def test_built_frontend_serves_index_assets_and_snapshots(monkeypatch, tmp_path):
    monkeypatch.setattr(web, "DIST_DIR", tmp_path, raising=False)
    (tmp_path / "index.html").write_text("<h1>new frontend</h1>")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets/app.js").write_text("console.log('built')")
    (tmp_path / "snapshots").mkdir()
    (tmp_path / "snapshots/example.json").write_text('{"live": true}')
    assert callable(getattr(web, "mount_static", None)), "static service is missing"
    app = FastAPI()
    app.include_router(web.router)
    web.mount_static(app)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/")).text == "<h1>new frontend</h1>"
        assert (await client.get("/assets/app.js")).text == "console.log('built')"
        assert (await client.get("/snapshots/example.json")).json() == {"live": True}
        assert (await client.get("/unknown")).status_code == 404
        assert (await client.get("/assets/")).status_code == 404


@pytest.mark.parametrize("path,location", [("review-queue", "/#/ops/review"),
                                          ("eval-runs", "/#/ops/evals"),
                                          ("faith-cases", "/#/ops/faith")])
async def test_admin_redirects(client, path, location):
    response = await client.get(f"/admin/{path}", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == location
