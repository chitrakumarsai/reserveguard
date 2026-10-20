"""Runtime settings, read from environment / .env."""

from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_LLM_MODEL = "claude-opus-5-5"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    airwallex_client_id: str = ""
    airwallex_api_key: SecretStr = SecretStr("")
    airwallex_base_url: str = "https://api.sandbox.airwallex.com"
    anthropic_api_key: SecretStr = SecretStr("")
    use_fake_airwallex: bool = True
    use_llm: bool = False
    llm_model: str = DEFAULT_LLM_MODEL


@lru_cache
def get_settings() -> Settings:
    return Settings()
