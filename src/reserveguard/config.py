"""Runtime settings, read from environment / .env."""

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_LLM_MODEL = "claude-opus-5-5"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    airwallex_client_id: str = ""
    airwallex_api_key: SecretStr = SecretStr("")
    airwallex_base_url: str = "https://api.sandbox.airwallex.com"
    anthropic_api_key: SecretStr = SecretStr("")
    # Bearer token for the HTTP API; while empty, every endpoint but /health returns 503.
    reserveguard_api_token: SecretStr = SecretStr("")
    use_fake_airwallex: bool = True
    use_llm: bool = False
    llm_model: str = DEFAULT_LLM_MODEL
    # Live mode keeps its ledger here so a restart loses nothing (fake mode stays in memory).
    reserveguard_db_path: Path = Path("data/reserveguard.db")


@lru_cache
def get_settings() -> Settings:
    return Settings()
