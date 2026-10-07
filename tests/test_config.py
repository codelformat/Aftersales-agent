import pytest
from pydantic import ValidationError

from app import config
from app.config import Settings


def _set_required(monkeypatch):
    monkeypatch.setenv("CHAT_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("CHAT_MODEL", "m1")
    monkeypatch.setenv("CHAT_API_KEY", "k1")
    monkeypatch.setenv("DATABASE_URL", "mysql+asyncmy://u:p@h:3307/aftersales")
    monkeypatch.setenv("EMBED_API_KEY", "e1")
    monkeypatch.setenv("RERANK_API_KEY", "r1")
    monkeypatch.setenv("MILVUS_URI", "http://m:19530")


def test_reads_chat_variables(monkeypatch):
    _set_required(monkeypatch)
    monkeypatch.setenv("CHAT_THINKING", "adaptive")
    s = Settings(_env_file=None)
    assert s.chat_base_url == "https://example.test/v1"
    assert s.chat_model == "m1"
    assert s.chat_api_key.get_secret_value() == "k1"
    assert s.chat_thinking == "adaptive"


def test_thinking_is_optional(monkeypatch):
    _set_required(monkeypatch)
    monkeypatch.delenv("CHAT_THINKING", raising=False)
    assert Settings(_env_file=None).chat_thinking is None


def test_unknown_env_file_keys_are_ignored(monkeypatch, tmp_path):
    for k in (
        "CHAT_BASE_URL", "CHAT_MODEL", "CHAT_API_KEY", "CHAT_THINKING", "DATABASE_URL",
        "EMBED_API_KEY", "RERANK_API_KEY", "MILVUS_URI",
    ):
        monkeypatch.delenv(k, raising=False)
    env = tmp_path / ".env"
    env.write_text(
        "CHAT_BASE_URL=https://example.test/v1\nCHAT_MODEL=m1\nCHAT_API_KEY=k1\n"
        "TOKEN_BUDGET=999\nDATABASE_URL=mysql+asyncmy://u:p@h:3307/aftersales\n"
        "EMBED_API_KEY=e1\nRERANK_API_KEY=r1\nMILVUS_URI=http://m:19530\n"
    )
    s = Settings(_env_file=env)
    assert s.chat_model == "m1"
    assert not hasattr(s, "token_budget")


def test_constants():
    assert config.TOKEN_BUDGET == 2000
    assert config.CHARS_PER_TOKEN == 2.0
    assert config.SHOP_NAME == "示例商城"
    assert config.UPSTREAM_TIMEOUT_SECONDS == 60
    assert config.UPSTREAM_MAX_RETRIES == 1
    assert config.MAX_INPUT_CHARS == 2000


def test_empty_thinking_treated_as_unset(monkeypatch):
    _set_required(monkeypatch)
    monkeypatch.setenv("CHAT_THINKING", "")
    assert Settings(_env_file=None).chat_thinking is None


def test_test_database_url_replaces_db_name():
    from app.config import test_database_url

    url = "mysql+asyncmy://aftersales:aftersales@127.0.0.1:3307/aftersales?charset=utf8mb4"
    assert test_database_url(url) == (
        "mysql+asyncmy://aftersales:aftersales@127.0.0.1:3307/aftersales_test?charset=utf8mb4"
    )


def test_tool_constants():
    from app import config

    assert config.TOOL_TIMEOUT_SECONDS == 5
    assert config.TOOL_MAX_ATTEMPTS == 3
    assert config.TOOL_RETRY_BASE_DELAY == 0.2
    assert config.TOOL_RETRY_MAX_DELAY == 2.0
    assert config.TOOL_RESULT_MAX_CHARS == 1500


def test_reads_knowledge_variables(monkeypatch):
    _set_required(monkeypatch)
    monkeypatch.delenv("EMBED_BASE_URL", raising=False)
    s = Settings(_env_file=None)
    assert s.embed_api_key.get_secret_value() == "e1"
    assert s.embed_base_url == "https://api.siliconflow.cn/v1"
    assert s.milvus_uri == "http://m:19530"


def test_knowledge_constants():
    assert config.EMBED_MODEL == "BAAI/bge-m3"
    assert config.EMBED_DIM == 1024
    assert config.KNOWLEDGE_COLLECTION == "knowledge"
    assert config.KNOWLEDGE_TEST_COLLECTION == "knowledge_test"
    assert config.CHUNK_MAX_CHARS == 400
    assert config.OVERLAP_MAX_CHARS == 100
    assert config.VECTORIZE_BATCH_SIZE == 16
    assert config.MINE_BATCH_SIZE == 20
    assert config.MINE_CONCURRENCY == 4
    assert config.DEDUP_KB_MIN_SCORE == 0.55
    assert config.DEDUP_STAGING_MIN_SCORE == 0.75
    assert config.MINED_CATEGORIES == ("退换货", "运费", "发票", "售后维修", "账户", "支付", "物流", "其他")


def test_reads_rerank_variables(monkeypatch):
    _set_required(monkeypatch)
    monkeypatch.delenv("RERANK_BASE_URL", raising=False)
    s = Settings(_env_file=None)
    assert s.rerank_api_key.get_secret_value() == "r1"
    assert s.rerank_base_url == "https://api.siliconflow.cn/v1"


def test_rerank_api_key_is_required(monkeypatch):
    _set_required(monkeypatch)
    monkeypatch.delenv("RERANK_API_KEY", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_ch04_constants():
    assert config.RERANK_MODEL == "BAAI/bge-reranker-v2-m3"
    assert config.RERANK_TIMEOUT_SECONDS == 10
    assert config.RERANK_MAX_ATTEMPTS == 3
    assert (config.RERANK_RETRY_BASE_DELAY, config.RERANK_RETRY_MAX_DELAY) == (0.5, 4.0)
    assert (config.RECALL_LEG_LIMIT, config.RRF_K, config.FUSED_LIMIT) == (50, 60, 50)
    assert config.EVIDENCE_TOP_N == 10
    assert config.RERANK_MIN_SCORE == 0.30
    assert config.QUERY_FAQ_TIMEOUT_SECONDS == 20
    assert config.PRODUCT_CATEGORIES == (
        "蓝牙耳机", "羊毛衫", "扫地机器人", "电动牙刷", "台灯", "保温杯", "运动鞋", "手机壳",
    )
    assert config.GENERAL_CATEGORY == "通用"
    assert config.KNOWLEDGE_TEXT_MAX_BYTES == 16384


def test_obsolete_faq_constants_are_removed():
    assert not hasattr(config, "FAQ_MIN_SCORE") and not hasattr(config, "FAQ_MAX_RESULTS")
