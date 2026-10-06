from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

TOKEN_BUDGET = 2000
# 实测 DeepSeek：55 个中文字符约 30 token。取 2.0 字符/token。
CHARS_PER_TOKEN = 2.0
SHOP_NAME = "示例商城"
UPSTREAM_TIMEOUT_SECONDS = 60
UPSTREAM_MAX_RETRIES = 1
MAX_INPUT_CHARS = 2000


class Settings(BaseSettings):
    # 忽略 .env 中未定义的变量。
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    chat_base_url: str
    chat_model: str
    chat_api_key: SecretStr
    chat_thinking: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
