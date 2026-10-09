from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

# 实测 DeepSeek（2026-10-09，evals/run_token_calibration.py）：中文正文 1.54–1.67 字符/token，取 1.5，估算略高于真实值。
CHARS_PER_TOKEN = 1.5
# 工具定义和工具结果是 JSON，实测 2.36–2.38 字符/token。
TOOL_SCHEMA_CHARS_PER_TOKEN = 2.3
SHOP_NAME = "示例商城"
UPSTREAM_TIMEOUT_SECONDS = 60
UPSTREAM_MAX_RETRIES = 1
MAX_INPUT_CHARS = 2000
TOOL_TIMEOUT_SECONDS = 5
MCP_DISCOVERY_TIMEOUT_SECONDS = 2
TOOL_POLICY_PATH = Path(__file__).resolve().parent.parent / "config" / "tools.json"
TOOL_MAX_ATTEMPTS = 3
TOOL_RETRY_BASE_DELAY = 0.2
TOOL_RETRY_MAX_DELAY = 2.0
AUDIT_WRITE_TIMEOUT_SECONDS = 1
AUDIT_SUMMARY_MAX_CHARS = 500
AUDIT_ERROR_MAX_CHARS = 512
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
CHECKPOINT_DB_PATH = str(Path(__file__).resolve().parent.parent / "data" / "checkpoints.sqlite")
# 限制意图识别等待时间，超时后走 business 出口。
INTENT_TIMEOUT_SECONDS = 8
RESOLVE_TIMEOUT_SECONDS = 8
RESOLVE_MESSAGE_MAX_CHARS = 200
EXPAND_TIMEOUT_SECONDS = 8
# 扩写查询条数上限，不含原查询。
EXPAND_MAX_QUERIES = 3
# 多查询合并后送重排的候选上限。
MULTI_FUSED_LIMIT = 50
# 小模型置信度低于门槛时，由大模型重判。
INTENT_SMALL_MODEL: str | None = None
INTENT_ESCALATE_BELOW = 0.7
USER_ORDER_COUNT = 3
# 一轮中 Agent 累计 token（输入 + 输出，含思考）。
AGENT_TOKEN_BUDGET = 16000
GRAPH_RECURSION_LIMIT = 25
# 置信度闸的 Top-1 重排分门槛。与检索门槛分开设置。
GATE_MIN_SCORE = 0.20
# ch08 接入 MCP 后，System + 工具定义实测 2047–2360 token（全部内置 Agent 工具 + 3 个 MCP 工具时最大）。
SYSTEM_RESERVE_TOKENS = 2400
EVIDENCE_ITEM_TOKENS = 250
SUMMARY_RESERVE_TOKENS = 400
SAFETY_MARGIN_RATIO = 0.05
STEP_OVERHEAD_TOKENS = 100
KEEP_TURNS = 30
# 默认配置 22 轮实测（2026-10-09）层 1 每轮平均增量约 250 token，向上取整到 50 再乘 1.2。
TURN_TOKENS = 300
LAYER1_RATIO = 0.7
LAYER2_RATIO = 0.3
LAYER1_LOW_WATER = 0.6
LAYER2_REPLY_CHARS = 60
LAYER2_TOOL_MAX_CHARS = 80
SUMMARY_TIMEOUT_SECONDS = 30
SUMMARY_MAX_CHARS = 300
SUMMARY_TOOL_RESULT_CHARS = 200


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
    model_context_window: int = 128000
    max_output_tokens: int = 8192
    # 覆盖 MAX_INPUT_CHARS=2000 字（按 1.5 字符/token 约 1339 token）。
    max_user_input_tokens: int = 1400
    max_agent_steps: int = 4
    tool_result_max_tokens: int = 750
    rerank_top_k: int = 10


def test_database_url(url: str) -> str:
    """把库名替换为 aftersales_test，其余部分不变。"""
    return make_url(url).set(database="aftersales_test").render_as_string(hide_password=False)


@lru_cache
def get_settings() -> Settings:
    return Settings()
