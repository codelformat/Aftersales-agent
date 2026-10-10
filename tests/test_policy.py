import json
import os
from dataclasses import replace

import pytest

from app.tools import policy as pol
from app.tools.registry import builtin_registry


def write(path, data, mtime=None):
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    if mtime:
        os.utime(path, ns=(mtime, mtime))


DATA = {"servers": {"logistics": {"url": "http://x/mcp", "tools": {"query_logistics": "read", "bad": "deny"}}},
        "overrides": {"query_order": {"timeout_seconds": 2, "max_retries": 1}}}


def test_env_policy_path_and_explicit_path_precedence(tmp_path, monkeypatch):
    from app.config import get_settings
    path = tmp_path / "environment.json"
    write(path, {"servers": {"custom": {"url": "http://custom/mcp", "tools": {"x": "read"}}},
                 "overrides": {}})
    monkeypatch.setenv("TOOL_POLICY_PATH", str(path))
    get_settings.cache_clear()
    try:
        assert pol.load_policy().mcp_permission("custom", "x") == "read"
        explicit = tmp_path / "explicit.json"
        write(explicit, DATA)
        assert pol.load_policy(explicit).mcp_permission("logistics", "query_logistics") == "read"
    finally:
        get_settings.cache_clear()


def test_policy_setting_defaults_to_none(monkeypatch):
    from app.config import Settings
    monkeypatch.delenv("TOOL_POLICY_PATH", raising=False)
    assert Settings().tool_policy_path is None


def test_permissions(tmp_path):
    p = tmp_path / "tools.json"
    write(p, DATA)
    policy = pol.load_policy(p)
    assert policy.mcp_permission("logistics", "query_logistics") == "read"
    assert policy.mcp_permission("logistics", "bad") == "deny"
    assert policy.mcp_permission("logistics", "new_tool") == "unlisted"
    assert policy.mcp_permission("other", "x") == "unlisted"


def test_reload_on_mtime_change(tmp_path):
    p = tmp_path / "tools.json"
    write(p, DATA, mtime=1_000_000_000)
    assert pol.load_policy(p).mcp_permission("logistics", "new_tool") == "unlisted"
    data = json.loads(json.dumps(DATA))
    data["servers"]["logistics"]["tools"]["new_tool"] = "read"
    write(p, data, mtime=2_000_000_000)
    assert pol.load_policy(p).mcp_permission("logistics", "new_tool") == "read"


def test_broken_file_keeps_last_good(tmp_path, caplog):
    p = tmp_path / "tools.json"
    write(p, DATA, mtime=1_000_000_000)
    good = pol.load_policy(p)
    p.write_text("{oops", encoding="utf-8")
    os.utime(p, ns=(2_000_000_000, 2_000_000_000))
    assert pol.load_policy(p) == good
    assert "tool_policy_invalid" in caplog.text


def test_missing_file_first_time_is_empty(tmp_path):
    assert pol.load_policy(tmp_path / "none.json") == pol.EMPTY_POLICY


def test_override_applies_to_builtin(tmp_path):
    p = tmp_path / "tools.json"
    write(p, DATA)
    entry = pol.apply_override(builtin_registry()["query_order"], pol.load_policy(p))
    assert (entry.timeout, entry.max_retries) == (2, 1)


def test_repo_policy_file_is_valid():
    policy = pol.load_policy()
    assert set(policy.servers) == {"logistics", "aftersales"}
    assert policy.mcp_permission("aftersales", "query_warranty") == "read"


def test_same_mtime_reuses_cached_policy(tmp_path):
    p = tmp_path / "tools.json"
    write(p, DATA, mtime=1_000_000_000)
    good = pol.load_policy(p)
    write(p, {"servers": {}, "overrides": {}}, mtime=1_000_000_000)
    assert pol.load_policy(p) is good


def test_default_path_is_read_at_call_time(tmp_path, monkeypatch):
    p = tmp_path / "tools.json"
    write(p, DATA)
    monkeypatch.setattr(pol, "TOOL_POLICY_PATH", p)
    assert pol.load_policy().mcp_permission("logistics", "query_logistics") == "read"


def test_server_order_and_unknown_permissions(tmp_path):
    p = tmp_path / "tools.json"
    write(p, {"servers": {
        "z": {"url": "http://z/mcp", "tools": {"write_tool": "write", "unknown": "admin", "malformed": []}},
        "a": {"url": "http://a/mcp", "tools": {}},
    }, "overrides": {}})
    policy = pol.load_policy(p)
    assert list(policy.servers) == ["z", "a"]
    assert policy.mcp_permission("z", "write_tool") == "write"
    assert policy.mcp_permission("z", "unknown") == "deny"
    assert policy.mcp_permission("z", "malformed") == "deny"


@pytest.mark.parametrize("invalid", [
    [],
    {},
    {"servers": [], "overrides": {}},
    {"servers": {"x": {"tools": {}}}, "overrides": {}},
    {"servers": {"x": {"url": 1, "tools": {}}}, "overrides": {}},
    {"servers": {"x": {"url": "http://x/mcp", "tools": []}}, "overrides": {}},
    {"servers": {}, "overrides": []},
    {"servers": {}, "overrides": {"query_order": []}},
    {"servers": {}, "overrides": {"query_order": {"timeout_seconds": "slow"}}},
    {"servers": {}, "overrides": {"query_order": {"max_retries": 1.5}}},
])
def test_invalid_structure_keeps_last_good(tmp_path, caplog, invalid):
    p = tmp_path / "tools.json"
    write(p, DATA, mtime=1_000_000_000)
    good = pol.load_policy(p)
    write(p, invalid, mtime=2_000_000_000)
    assert pol.load_policy(p) is good
    assert "tool_policy_invalid" in caplog.text


def test_broken_file_first_time_is_empty(tmp_path, caplog):
    p = tmp_path / "tools.json"
    p.write_text("{oops", encoding="utf-8")
    assert pol.load_policy(p) == pol.EMPTY_POLICY
    assert "tool_policy_invalid" in caplog.text


def test_removed_file_keeps_last_good(tmp_path, caplog):
    p = tmp_path / "tools.json"
    write(p, DATA)
    good = pol.load_policy(p)
    p.unlink()
    assert pol.load_policy(p) is good
    assert "tool_policy_invalid" in caplog.text


@pytest.mark.parametrize("source", ["builtin", "mcp"])
def test_override_preserves_original_and_unspecified_fields(tmp_path, source):
    p = tmp_path / "tools.json"
    write(p, {"servers": {}, "overrides": {"query_order": {"max_retries": 0}}})
    original = replace(builtin_registry()["query_order"], source=source)
    updated = pol.apply_override(original, pol.load_policy(p))
    assert updated == replace(original, max_retries=0)
    assert original.max_retries == 2
    assert updated.timeout == original.timeout


def test_no_override_returns_original():
    original = builtin_registry()["query_order"]
    assert pol.apply_override(original, pol.EMPTY_POLICY) is original
