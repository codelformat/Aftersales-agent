from pathlib import Path

from fastapi import APIRouter, FastAPI
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

router = APIRouter()
DIST_DIR = Path(__file__).resolve().parent.parent / "web" / "dist"


@router.get("/", include_in_schema=False)
async def index():
    if (DIST_DIR / "index.html").is_file():
        return FileResponse(DIST_DIR / "index.html", headers={"Cache-Control": "no-cache"})
    return HTMLResponse("""<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<title>构建前端</title><h1>前端尚未构建</h1>
<p>在仓库根目录执行：</p>
<pre>npm --prefix web ci &amp;&amp; npm --prefix web run build</pre>
<p>构建后重启服务，再刷新此页。</p></html>""")


@router.get("/admin/faith-cases", include_in_schema=False)
async def faith_cases_page():
    return RedirectResponse("/#/ops/faith", status_code=307)


@router.get("/admin/review-queue", include_in_schema=False)
async def review_queue_page():
    return RedirectResponse("/#/ops/review", status_code=307)


@router.get("/admin/eval-runs", include_in_schema=False)
async def eval_runs_page():
    return RedirectResponse("/#/ops/evals", status_code=307)


def mount_static(app: FastAPI) -> None:
    """在 API 和页面路由后挂载全部构建资源。"""
    if DIST_DIR.is_dir():
        app.mount("/", StaticFiles(directory=DIST_DIR, html=False), name="web-static")
