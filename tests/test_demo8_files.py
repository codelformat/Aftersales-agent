import ast
import importlib.util
from pathlib import Path
import re
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"


def test_demo8_shell_syntax():
    for name in ("demo8.sh", "demo5.sh", "demo6.sh", "demo7.sh"):
        path = SCRIPTS / name
        assert path.is_file(), f"缺少 {name}"
        result = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True, cwd=ROOT)
        assert result.returncode == 0, result.stderr


def test_demo8_shell_rules():
    script = (SCRIPTS / "demo8.sh").read_text(encoding="utf-8")
    assert "set -euo pipefail" in script
    assert not re.search(r"\$[A-Za-z_][A-Za-z0-9_]*[^\x00-\x7F]", script)
    assert "mapfile" not in script and "declare -A" not in script
    assert "trap 'rc=$?;" in script
    assert "tools.json" in script and "--noproxy" in script


def test_demo5_6_7_check_logistics_port():
    for name in ("demo5.sh", "demo6.sh", "demo7.sh"):
        script = (SCRIPTS / name).read_text(encoding="utf-8")
        assert "8101" in script and "mcp_servers.logistics" in script, name


def test_demo8_python_syntax(tmp_path, monkeypatch):
    paths = [SCRIPTS / "demo8_chat.py", *sorted((SCRIPTS / "demo8_assets").glob("*.py"))]
    assert len(paths) == 3
    for path in paths:
        ast.parse(path.read_text(encoding="utf-8"))
    monkeypatch.setenv("PYTHONPYCACHEPREFIX", str(tmp_path / "pycache"))
    result = subprocess.run([sys.executable, "-m", "py_compile", *map(str, paths)],
                            capture_output=True, text=True, cwd=ROOT)
    assert result.returncode == 0, result.stderr


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.anyio
async def test_member_points_asset_registers_tool():
    from app.tools import registry

    module = _load(SCRIPTS / "demo8_assets" / "query_member_points.py", "demo8_points")
    try:
        entry = registry._ENTRIES["query_member_points"]
        assert entry.parameters["properties"]["phone"]["pattern"] == r"^1\d{10}$"
        result = await module.query_member_points.ainvoke({"phone": "13800001234"})
        assert set(result) == {"phone", "points", "expiring"}
    finally:
        registry._unregister("query_member_points")


@pytest.mark.anyio
async def test_repair_progress_asset_registers_mcp_tool():
    from mcp.server.fastmcp import FastMCP

    module = _load(SCRIPTS / "demo8_assets" / "query_repair_progress.py", "demo8_repair")
    result = await module.query_repair_progress("1001")
    assert result["found"] is True
    assert result["status_code"] in {"RECEIVED", "DIAGNOSING", "REPAIRING", "SHIPPED_BACK"}
    assert (await module.query_repair_progress("9001"))["found"] is False
    mcp = FastMCP("t")
    module.register(mcp)
    assert "query_repair_progress" in {t.name for t in await mcp.list_tools()}
