from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    openclaw_transport: Literal["telegram", "direct"] = "telegram"
    openclaw_base_url: str = "http://host.docker.internal:18789"
    openclaw_gateway_token: str = ""
    openclaw_agent: str = "openclaw/default"
    openclaw_session_user: str = "even-homebuddy-glasses"
    openclaw_session_state_path: str = "/data/openclaw-session.json"
    openclaw_timeout_seconds: float = Field(default=240, gt=0)

    telegram_api_id: int = 0
    telegram_api_hash: str = ""
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

    @field_validator("openclaw_base_url")
    @classmethod
    def normalize_openclaw_base_url(cls, value: str) -> str:
        return value.strip().rstrip("/")

    @model_validator(mode="after")
    def validate_selected_transport(self) -> "Settings":
        if self.openclaw_transport == "direct":
            if not self.openclaw_base_url:
                raise ValueError("OPENCLAW_BASE_URL is required in direct mode")
            if len(self.openclaw_gateway_token) < 16:
                raise ValueError(
                    "OPENCLAW_GATEWAY_TOKEN must contain at least 16 characters "
                    "in direct mode"
                )
            if not self.openclaw_session_user.strip():
                raise ValueError(
                    "OPENCLAW_SESSION_USER is required in direct mode"
                )
            if not self.openclaw_session_state_path.strip():
                raise ValueError(
                    "OPENCLAW_SESSION_STATE_PATH is required in direct mode"
                )
        else:
            if self.telegram_api_id <= 0 or not self.telegram_api_hash:
                raise ValueError(
                    "TELEGRAM_API_ID and TELEGRAM_API_HASH are required in "
                    "telegram mode"
                )
            if self.telegram_target_id <= 0:
                raise ValueError(
                    "TELEGRAM_TARGET_ID must be set in telegram mode"
                )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()

