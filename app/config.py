from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    telegram_api_id: int
    telegram_api_hash: str = Field(min_length=1)
    telegram_session_path: str = "/data/telegram-user"
    telegram_target_id: int = 0

    proxy_bearer_token: str = Field(min_length=16)
    telegram_response_timeout_seconds: float = Field(
        default=240,
        gt=0,
    )
    telegram_settle_seconds: float = Field(
        default=2.5,
        ge=0.25,
    )
    max_request_chars: int = Field(default=12000, ge=1)
    log_level: str = "INFO"

    @field_validator("telegram_target_id", mode="before")
    @classmethod
    def empty_target_id_is_zero(cls, value: object) -> object:
        if value in (None, ""):
            return 0
        return value

    @field_validator("log_level")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        return value.upper()


@lru_cache
def get_settings() -> Settings:
    return Settings()

