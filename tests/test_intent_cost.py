import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from pydantic import SecretStr

from scripts import intent_cost
from scripts.intent_cost import IntentCost, Obs, aggregate, fetch, parse_observation, render


def test_parse_observation_root_and_generation():
    root = parse_observation({
        "traceId": "t1", "parentObservationId": None, "isRootObservation": True,
        "type": "CHAIN", "metadata": {"intent": "物流"},
    })
    gen = parse_observation({
        "traceId": "t1", "parentObservationId": "p", "type": "GENERATION",
        "usageDetails": {"input": 100, "output": 20, "total": 120},
    })
    assert root == Obs("t1", True, "物流", 0, 0, 0)
    assert gen == Obs("t1", False, None, 100, 20, 120)


@pytest.mark.parametrize("usage, expected", [
    ({"input": 247, "output": 15, "total": 646, "input_cache_read": 384},
     (631, 15, 646)),
    ({"input": 136, "output": 71, "total": 2347, "input_cache_read": 2048,
      "output_reasoning": 92}, (2184, 163, 2347)),
])
@pytest.mark.parametrize("include_total", [True, False])
def test_parse_observation_includes_cached_input_and_reasoning_output(usage, expected, include_total):
    if not include_total:
        usage = {key: value for key, value in usage.items() if key != "total"}
    observation = parse_observation({
        "traceId": "t1", "parentObservationId": "p", "type": "GENERATION",
        "usageDetails": usage,
    })
    assert (observation.input, observation.output, observation.total) == expected


def test_parse_observation_preserves_explicit_zero_total():
    observation = parse_observation({
        "traceId": "t1", "usageDetails": {"input": 3, "output": 4, "total": 0},
    })
    assert observation.total == 0


@pytest.mark.parametrize("metadata", [None, {}, "invalid"])
def test_parse_observation_missing_metadata_and_usage(metadata):
    assert parse_observation({
        "traceId": "t1", "parentObservationId": None, "metadata": metadata,
        "usageDetails": None,
    }) == Obs("t1", True, None, 0, 0, 0)


def test_parse_observation_uses_explicit_root_flag():
    assert not parse_observation({
        "traceId": "t1", "parentObservationId": None, "isRootObservation": False,
    }).is_root


def test_aggregate_groups_by_trace_intent_and_excludes_traces_without_roots():
    roots = [
        Obs("t1", True, "物流", 0, 0, 0),
        Obs("t2", True, "商品咨询", 0, 0, 0),
        Obs("t3", True, "物流", 0, 0, 0),
    ]
    generations = [
        Obs("t1", False, None, 100, 20, 120),
        Obs("t1", False, None, 50, 5, 55),
        Obs("t2", False, None, 900, 100, 1000),
        Obs("t3", False, None, 10, 0, 10),
        Obs("t4", False, None, 7, 1, 8),
    ]
    rows = aggregate(roots, generations)
    assert [(r.intent, r.turns, r.total_tokens) for r in rows] == [
        ("商品咨询", 1, 1000), ("物流", 2, 185),
    ]
    assert rows[1].input_tokens == 160
    assert rows[1].output_tokens == 25
    assert rows[1].avg_tokens == 92.5
    assert rows[0].share == pytest.approx(1000 / 1185)
    assert sum(r.share for r in rows) == pytest.approx(1.0)


@pytest.mark.parametrize("intents", [
    [None, "物流", None], ["物流", None, "-"], ["-", "物流", None],
])
def test_aggregate_resumed_trace_counts_once_and_keeps_known_intent(intents):
    roots = [Obs("t1", True, intent, 500, 100, 600) for intent in intents]
    generations = [Obs("t1", False, None, 100, 20, 120)]
    assert aggregate(roots, generations) == [IntentCost("物流", 1, 100, 20, 120, 120.0, 1.0)]


def test_aggregate_missing_intent_and_zero_token_turns():
    roots = [Obs("t1", True, None, 0, 0, 0), Obs("t2", True, "闲聊", 0, 0, 0)]
    assert aggregate(roots, []) == [
        IntentCost("-", 1, 0, 0, 0, 0.0, 0.0),
        IntentCost("闲聊", 1, 0, 0, 0, 0.0, 0.0),
    ]
    assert aggregate([], [Obs("background", False, None, 100, 20, 120)]) == []


def test_render_marks_top_and_formats_average_and_share():
    rows = aggregate(
        [Obs("t1", True, "物流", 0, 0, 0), Obs("t2", True, "闲聊", 0, 0, 0)],
        [Obs("t1", False, None, 3, 1, 4), Obs("t2", False, None, 1, 1, 2)],
    )
    text = render(rows, 7)
    assert "最近 7 天" in text and "chat_turn" in text
    assert "| 输入 token（含缓存） | 输出 token（含思考） |" in text
    assert "| 物流（最费 token） | 1 | 3 | 1 | 4 | 4 | 66.7% |" in text
    assert "| 闲聊 | 1 | 1 | 1 | 2 | 2 | 33.3% |" in text
    assert text.count("最费 token") == 1


def install_transport(monkeypatch, handler):
    real_client = httpx.Client

    def client(**kwargs):
        assert kwargs["trust_env"] is False
        return real_client(**kwargs, transport=httpx.MockTransport(handler))

    monkeypatch.setattr(intent_cost.httpx, "Client", client)


def test_fetch_queries_roots_and_generations_with_independent_cursors(monkeypatch):
    requests = []
    pages = [
        {"data": [{"traceId": "t1", "parentObservationId": None,
                   "isRootObservation": True, "type": "CHAIN", "metadata": {"intent": "物流"}}],
         "meta": {"cursor": "root-next"}},
        {"data": [{"traceId": "t2", "parentObservationId": None,
                   "isRootObservation": True, "type": "CHAIN", "metadata": {}}],
         "meta": {"cursor": None}},
        {"data": [], "meta": {"cursor": "generation-next"}},
        {"data": [{"traceId": "t1", "parentObservationId": "p", "type": "GENERATION",
                   "usageDetails": {"input": 10, "output": 2, "total": 12}},
                  {"traceId": "background", "parentObservationId": "p", "type": "GENERATION",
                   "usageDetails": {"input": 999, "output": 1, "total": 1000}}],
         "meta": {"cursor": None}},
    ]

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=pages[len(requests) - 1])

    install_transport(monkeypatch, handler)
    roots, generations = fetch("http://langfuse.test/", ("pk", "sk"), 7)
    assert roots == [Obs("t1", True, "物流", 0, 0, 0), Obs("t2", True, None, 0, 0, 0)]
    assert generations == [Obs("t1", False, None, 10, 2, 12),
                           Obs("background", False, None, 999, 1, 1000)]
    assert len(requests) == 4
    for index, request in enumerate(requests):
        assert request.url.path == "/api/public/v2/observations"
        assert request.headers["Authorization"] == "Basic cGs6c2s="
        params = request.url.params
        assert params["limit"] == "1000"
        assert "page" not in params
        assert params["fromStartTime"] == requests[0].url.params["fromStartTime"]
        assert params["toStartTime"] == requests[0].url.params["toStartTime"]
        if index < 2:
            assert params["isRootObservation"] == "true"
            assert params["fields"] == "core,basic,metadata"
            assert json.loads(params["filter"]) == [
                {"type": "string", "column": "traceName", "operator": "=", "value": "chat_turn"},
            ]
        else:
            assert "isRootObservation" not in params
            assert params["fields"] == "core,usage"
            assert json.loads(params["filter"]) == [
                {"type": "string", "column": "type", "operator": "=", "value": "GENERATION"},
            ]
    assert [r.url.params.get("cursor") for r in requests] == [
        None, "root-next", None, "generation-next",
    ]
    start = datetime.fromisoformat(requests[0].url.params["fromStartTime"])
    end = datetime.fromisoformat(requests[0].url.params["toStartTime"])
    assert end - start == timedelta(days=7)
    assert end.utcoffset() == timedelta(0)
    assert aggregate(roots, generations) == [
        IntentCost("物流", 1, 10, 2, 12, 12.0, 1.0), IntentCost("-", 1, 0, 0, 0, 0.0, 0.0),
    ]


def test_fetch_raises_on_http_failure(monkeypatch):
    install_transport(monkeypatch, lambda request: httpx.Response(401, json={"message": "Unauthorized"}))
    with pytest.raises(httpx.HTTPStatusError):
        fetch("http://langfuse.test", ("pk", "sk"), 1)


@pytest.mark.parametrize("missing", ["langfuse_public_key", "langfuse_secret_key", "langfuse_base_url"])
def test_main_missing_config_returns_one(monkeypatch, capsys, missing):
    settings = dict(langfuse_public_key="pk", langfuse_secret_key=SecretStr("sk"),
                    langfuse_base_url="http://langfuse.test")
    settings[missing] = None
    monkeypatch.setattr(intent_cost, "get_settings", lambda: SimpleNamespace(**settings))
    monkeypatch.setattr("sys.argv", ["intent_cost.py"])
    assert intent_cost.main() == 1
    assert "未配置 Langfuse" in capsys.readouterr().out


def test_main_empty_window(monkeypatch, capsys):
    monkeypatch.setattr(intent_cost, "get_settings", lambda: SimpleNamespace(
        langfuse_public_key="pk", langfuse_secret_key=SecretStr("sk"),
        langfuse_base_url="http://langfuse.test"))
    monkeypatch.setattr("sys.argv", ["intent_cost.py", "--days", "1"])
    install_transport(monkeypatch, lambda request: httpx.Response(200, json={"data": [], "meta": {"cursor": None}}))
    assert intent_cost.main() == 0
    assert "时间窗内没有 chat_turn 数据" in capsys.readouterr().out
