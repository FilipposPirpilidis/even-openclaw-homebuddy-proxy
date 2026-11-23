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

    location_context_enabled: bool = False
    location_state_path: str = "/data/client-location.json"
    location_markdown_path: str = "/openclaw-location/latest.md"
    location_max_age_seconds: float = Field(default=86400, gt=0)
    location_display_timezone: str = "Europe/Athens"
    location_enrichment_enabled: bool = True
    location_enrichment_timeout_seconds: float = Field(default=20, gt=0)
    location_enrichment_cache_seconds: float = Field(default=900, ge=0)
    location_nearby_radius_meters: int = Field(default=250, ge=25, le=2000)
    location_nominatim_base_url: str = "https://nominatim.openstreetmap.org"
    location_overpass_url: str = "https://overpass-api.de/api/interpreter"
    location_osm_user_agent: str = (
        "HomeBuddy-OpenClaw/1.0 "
        "(+https://github.com/FilipposPirpilidis/"
        "even-openclaw-homebuddy-proxy)"
    )
    location_osm_language: str = "el,en"

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

    @field_validator("location_nominatim_base_url")
    @classmethod
    def normalize_nominatim_base_url(cls, value: str) -> str:
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
            if (
                self.location_context_enabled
                and not self.location_state_path.strip()
            ):
                raise ValueError(
                    "LOCATION_STATE_PATH is required when location context is enabled"
                )
            if (
                self.location_context_enabled
                and not self.location_markdown_path.strip()
            ):
                raise ValueError(
                    "LOCATION_MARKDOWN_PATH is required when location is enabled"
                )
            if (
                self.location_enrichment_enabled
                and not self.location_osm_user_agent.strip()
            ):
                raise ValueError(
                    "LOCATION_OSM_USER_AGENT is required for OSM enrichment"
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

