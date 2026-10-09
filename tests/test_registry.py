import textwrap

import pytest

from app.tools import registry


def test_builtin_scan_registers_production_tools():
    names = list(registry.builtin_registry())
    assert set(names) == {"query_order", "query_product", "query_logistics", "query_faq", "create_ticket",
                          "offer_human_options", "offer_refund_form"}


def test_entry_fields():
    reg = registry.builtin_registry()
    t = reg["create_ticket"]
    assert (t.permission, t.max_retries, t.inject_conversation_id, t.source) == ("write", 0, True, "builtin")
    assert "conversation_id" not in t.parameters["properties"]
    assert set(t.parameters["required"]) == {"description", "ticket_type"}
    q = reg["query_order"]
    assert (q.permission, q.max_retries, q.agent) == ("read", 2, True)
    assert reg["query_faq"].agent is False
    assert q.openai_tool()["function"]["name"] == "query_order"


def test_new_file_in_package_is_registered(tmp_path, monkeypatch):
    pkg = tmp_path / "plugpkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "points.py").write_text(textwrap.dedent('''
        from langchain_core.tools import tool
        from app.tools.registry import register

        @register()
        @tool("query_points_test")
        async def query_points_test(phone: str) -> dict:
            """查积分。"""
            return {"points": 1}
    '''))
    monkeypatch.syspath_prepend(str(tmp_path))
    before = set(registry.builtin_registry())
    registry.load_package("plugpkg")
    assert set(registry.builtin_registry()) - before == {"query_points_test"}
    registry._unregister("query_points_test")


def test_builtin_toolset_opens_everything():
    from app.tools.toolset import builtin_toolset

    ts = builtin_toolset()
    assert "query_faq" in ts.entries and ts.closed == {}
    assert "query_faq" not in [t["function"]["name"] for t in ts.agent_tools()]


def test_ch04_tools_unchanged():
    assert [t.name for t in registry.ch04_tools()] == list(registry.CH04_CHAT_TOOLS)
