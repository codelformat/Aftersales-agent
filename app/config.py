from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

TOKEN_BUDGET = 2000
# 实测 DeepSeek：55 个中文字符约 30 token。取 2.0 字符/token。
CHARS_PER_TOKEN = 2.0
SHOP_NAME = "示例商城"
UPSTREAM_TIMEOUT_SECONDS = 60
UPSTREAM_MAX_RETRIES = 1
MAX_INPUT_CHARS = 2000
TOOL_TIMEOUT_SECONDS = 5
TOOL_MAX_ATTEMPTS = 3
TOOL_RETRY_BASE_DELAY = 0.2
TOOL_RETRY_MAX_DELAY = 2.0
TOOL_RESULT_MAX_CHARS = 1500
EMBED_MODEL = "BAAI/bge-m3"
EMBED_DIM = 1024
EMBED_TIMEOUT_SECONDS = 10
EMBED_MAX_RETRIES = 2
KNOWLEDGE_COLLECTION = "knowledge"
KNOWLEDGE_TEST_COLLECTION = "knowledge_test"
CHUNK_MAX_CHARS = 400
OVERLAP_MAX_CHARS = 100
VECTORIZE_BATCH_SIZE = 16
MILVUS_MAX_ATTEMPTS = 3
MILVUS_RETRY_BASE_DELAY = 0.5
MILVUS_RETRY_MAX_DELAY = 4.0
MINE_BATCH_SIZE = 20
MINE_CONCURRENCY = 4
DEDUP_KB_MIN_SCORE = 0.55
DEDUP_STAGING_MIN_SCORE = 0.75
MINED_CATEGORIES = ("退换货", "运费", "发票", "售后维修", "账户", "支付", "物流", "其他")
RERANK_MODEL = "BAAI/bge-reranker-v2-m3"
RERANK_TIMEOUT_SECONDS = 10
RERANK_MAX_ATTEMPTS = 3
RERANK_RETRY_BASE_DELAY = 0.5
RERANK_RETRY_MAX_DELAY = 4.0
RECALL_LEG_LIMIT = 50
RRF_K = 60
FUSED_LIMIT = 50
EVIDENCE_TOP_N = 10
# 门槛只挡明显无关的证据，能否回答由自评判断。
# 0.40（评估集 Top-1 保留率 ≥ 95% 的最高值）误伤宽泛口语问题，实测正确证据 0.31–0.37。
RERANK_MIN_SCORE = 0.20
QUERY_FAQ_TIMEOUT_SECONDS = 20
# 限制改写等待时间，超时后用原话检索。
QUERY_REWRITE_TIMEOUT_SECONDS = 8
# 限制自评等待时间，超时后按通过处理。
SELF_CHECK_TIMEOUT_SECONDS = 10
PRODUCT_CATEGORIES = ("蓝牙耳机", "羊毛衫", "扫地机器人", "电动牙刷", "台灯", "保温杯", "运动鞋", "手机壳")
GENERAL_CATEGORY = "通用"
# Milvus VARCHAR 的 max_length 按字节计。
KNOWLEDGE_TEXT_MAX_BYTES = 16384


class Settings(BaseSettings):
    # 忽略 .env 中未定义的变量。
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_ignore_empty=True)

    chat_base_url: str
    chat_model: str
    chat_api_key: SecretStr
    chat_thinking: str | None = None
    database_url: str
    embed_api_key: SecretStr
    embed_base_url: str = "https://api.siliconflow.cn/v1"
    milvus_uri: str
    rerank_api_key: SecretStr
    rerank_base_url: str = "https://api.siliconflow.cn/v1"


def test_database_url(url: str) -> str:
    """把库名替换为 aftersales_test，其余部分不变。"""
    return make_url(url).set(database="aftersales_test").render_as_string(hide_password=False)


@lru_cache
def get_settings() -> Settings:
    return Settings()
