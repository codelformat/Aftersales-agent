import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
import yaml
from mcp.server.fastmcp import FastMCP

from app.tools.policy import load_policy

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SERVICES = {"mysql", "milvus-etcd", "milvus-minio", "milvus-standalone"}
FULL_SERVICES = {"init", "mcp-logistics", "mcp-aftersales", "app"}


@pytest.mark.parametrize("name,port", [("logistics", 8101), ("aftersales", 8102)])
@pytest.mark.parametrize("host", [None, "0.0.0.0"])
def test_mcp_cli_bind_host(monkeypatch, name, port, host):
    cli = importlib.import_module(f"mcp_servers.{name}.__main__")
    started = []

    def capture_run(server, *, transport):
        started.append((server.settings.host, server.settings.port, transport))

    # Keep the real parser and server; replace only the blocking network listener.
    monkeypatch.setattr(FastMCP, "run", capture_run)
    argv = [name] + ([] if host is None else ["--host", host])
    monkeypatch.setattr(sys, "argv", argv)
    cli.main()
    assert started == [(host or "127.0.0.1", port, "streamable-http")]


def test_docker_tool_policy_only_changes_urls():
    docker = load_policy(ROOT / "config/tools.docker.json")
    local = load_policy(ROOT / "config/tools.json")
    assert set(docker.servers) == {"logistics", "aftersales"}
    assert docker.overrides == local.overrides
    for name, url in {
        "logistics": "http://mcp-logistics:8101/mcp",
        "aftersales": "http://mcp-aftersales:8102/mcp",
    }.items():
        assert docker.servers[name].url == url
        assert docker.servers[name].tools == local.servers[name].tools
        for tool, permission in local.servers[name].tools.items():
            assert docker.mcp_permission(name, tool) == permission
    local_data = json.loads((ROOT / "config/tools.json").read_text())
    docker_data = json.loads((ROOT / "config/tools.docker.json").read_text())
    for name in local_data["servers"]:
        docker_data["servers"][name]["url"] = local_data["servers"][name]["url"]
    assert docker_data == local_data


@pytest.fixture
def services():
    return yaml.safe_load((ROOT / "docker-compose.yml").read_text())["services"]


def test_compose_preserves_default_and_adds_full_profile(services):
    assert {name for name, svc in services.items() if not svc.get("profiles")} == DEFAULT_SERVICES
    assert set(services) == DEFAULT_SERVICES | FULL_SERVICES
    for name in FULL_SERVICES:
        assert services[name]["profiles"] == ["full"]


def test_compose_app_waits_for_initialization_and_mcp_health(services):
    assert "app" in services
    app = services["app"]
    assert app["ports"] == ["8000:8000"]
    assert app["depends_on"]["init"]["condition"] == "service_completed_successfully"
    for name in ("mcp-logistics", "mcp-aftersales"):
        assert app["depends_on"][name]["condition"] == "service_healthy"
        assert services[name]["healthcheck"]["test"]
        assert "--host" in services[name]["command"]
        assert "0.0.0.0" in services[name]["command"]


def test_compose_passes_mock_delay_to_mcp_tools(services):
    for name in ("mcp-logistics", "mcp-aftersales"):
        assert services[name].get("environment", {}).get("MOCK_DELAY_SECONDS") == "${MOCK_DELAY_SECONDS:-0}"


@pytest.mark.parametrize("name", ["init", "app"])
def test_compose_container_network_settings(services, name):
    assert name in services
    env = services[name]["environment"]
    assert env["DATABASE_URL"] == "mysql+asyncmy://aftersales:aftersales@mysql:3306/aftersales?charset=utf8mb4"
    assert env["MILVUS_URI"] == "http://milvus-standalone:19530"
    assert env["TOOL_POLICY_PATH"] == "/app/config/tools.docker.json"
    assert services[name]["env_file"]


def test_docker_init_bash_syntax():
    result = subprocess.run(["bash", "-n", str(ROOT / "scripts/docker_init.sh")], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.fixture
def init_repo(tmp_path):
    script = ROOT / "scripts/docker_init.sh"
    (tmp_path / "scripts").mkdir()
    if script.is_file():
        shutil.copy(script, tmp_path / "scripts/docker_init.sh")
    (tmp_path / "bin").mkdir()
    # Run the real inline Python. Replace only external database/HTTP operations.
    wrapper = tmp_path / "bin/uv"
    wrapper.write_text(f"#!{sys.executable}\n" + '''
import asyncio
import json
from pathlib import Path
import sys
import httpx
import sqlalchemy.ext.asyncio as sa
from sqlalchemy.exc import OperationalError

assert sys.argv[1:] == ["run", "python", "-"]
events = Path("events.jsonl")
def record(event):
    with events.open("a") as f:
        f.write(json.dumps(event) + "\\n")

class Connection:
    async def __aenter__(self):
        record("mysql")
        return self
    async def __aexit__(self, *args):
        pass
    async def execute(self, statement):
        assert str(statement) == "SELECT 1"
        if json.loads(Path("state.json").read_text()).get("mysql_down"):
            raise OperationalError("SELECT 1", {}, RuntimeError("not ready"))

class Engine:
    def connect(self):
        return Connection()
    async def dispose(self):
        pass

def create_engine(url, **kwargs):
    assert url == "mysql+asyncmy://aftersales:aftersales@mysql:3306/aftersales?charset=utf8mb4"
    return Engine()
sa.create_async_engine = create_engine

async def get(self, url, **kwargs):
    assert str(url) == "http://milvus-standalone:9091/healthz"
    record("milvus")
    state = json.loads(Path("state.json").read_text())
    failed = state.pop("milvus_retry", False)
    Path("state.json").write_text(json.dumps(state))
    return httpx.Response(503 if failed else 200, request=httpx.Request("GET", url))
httpx.AsyncClient.get = get

async def sleep(delay):
    record(["sleep", delay])
asyncio.sleep = sleep
exec(compile(sys.stdin.read(), "docker_init", "exec"), {"__name__": "__main__"})
''')
    wrapper.chmod(0o755)
    (tmp_path / "scripts/build_kb.py").write_text('''
import json
from pathlib import Path
import sys
state_file = Path("state.json")
state = json.loads(state_file.read_text())
with Path("events.jsonl").open("a") as f:
    f.write(json.dumps(["kb", *sys.argv[1:]]) + "\\n")
if "--status" in sys.argv:
    if state.get("status_error"):
        sys.exit(1)
    print(state["status"])
elif "--check" in sys.argv:
    sys.exit(1 if state.get("check_error") else 0)
else:
    if state.get("build_error"):
        sys.exit(1)
    state["status"] = "知识库状态：\\n  MySQL 合计 10（pending 0，done 10）；Milvus 实体数 10"
    state_file.write_text(json.dumps(state))
''')

    def run(state=None):
        assert script.is_file(), "Docker initialization script is missing"
        if state is not None:
            (tmp_path / "state.json").write_text(json.dumps(state))
        env = {
            **os.environ,
            "PATH": f"{tmp_path / 'bin'}:{os.environ['PATH']}",
            "PYTHONPATH": str(ROOT),
            "CHAT_BASE_URL": "https://example.invalid/v1",
            "CHAT_MODEL": "test", "CHAT_API_KEY": "test",
            "EMBED_API_KEY": "test", "RERANK_API_KEY": "test",
            "DATABASE_URL": "mysql+asyncmy://aftersales:aftersales@mysql:3306/aftersales?charset=utf8mb4",
            "MILVUS_URI": "http://milvus-standalone:19530",
        }
        result = subprocess.run(["bash", str(tmp_path / "scripts/docker_init.sh")],
                                cwd="/", env=env, capture_output=True, text=True, timeout=15)
        events_path = tmp_path / "events.jsonl"
        events = [json.loads(line) for line in events_path.read_text().splitlines()] if events_path.exists() else []
        return result, events

    return run


@pytest.mark.parametrize("status", [
    "知识库状态：\n  MySQL 合计 0（pending 0，done 0）；Milvus 实体数 0",
    "知识库状态：\n  MySQL 合计 10（pending 4，done 6）；Milvus 实体数 6",
])
def test_docker_init_builds_empty_or_pending_kb_then_skips_on_rerun(init_repo, status):
    result, events = init_repo({"status": status, "milvus_retry": True})
    assert result.returncode == 0, result.stderr
    kb_events = [e for e in events if isinstance(e, list) and e[0] == "kb"]
    assert kb_events == [["kb", "--status"], ["kb"], ["kb", "--check"]]
    assert events.index("mysql") < events.index("milvus") < events.index(["kb", "--status"])
    assert events.count("milvus") == 2
    assert any(isinstance(e, list) and e[0] == "sleep" and e[1] > 0 for e in events)
    result, events = init_repo()
    assert result.returncode == 0, result.stderr
    assert [e for e in events if isinstance(e, list) and e[0] == "kb"] == kb_events + [["kb", "--status"]]


@pytest.mark.parametrize("failure", ["status_error", "build_error", "check_error", "mysql_down"])
def test_docker_init_propagates_failures(init_repo, failure):
    result, events = init_repo({
        "status": "知识库状态：\n  MySQL 合计 0（pending 0，done 0）；Milvus 实体数 0",
        failure: True,
    })
    assert result.returncode != 0
    if failure == "mysql_down":
        assert "milvus" not in events
        assert ["kb", "--status"] not in events
    elif failure == "status_error":
        assert ["kb"] not in events


def test_docker_init_rejects_unrecognized_status(init_repo):
    result, events = init_repo({"status": "unexpected output"})
    assert result.returncode != 0
    assert ["kb"] not in events
