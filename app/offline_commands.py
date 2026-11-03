import asyncio
import json
import logging
import os
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx


logger = logging.getLogger(__name__)

NAME_PATTERN = re.compile(r"^[a-z0-9_]+$")
TRAILING_PUNCTUATION = re.compile(r"[\s.,;:!?·—–-]+$")


def normalize_offline_command(text: str) -> str:
    """Return a stable form for exact, accent-insensitive matching."""
    normalized = unicodedata.normalize("NFKD", text).casefold()
    normalized = "".join(
        character
        for character in normalized
        if not unicodedata.combining(character)
    )
    normalized = " ".join(normalized.split())
    return TRAILING_PUNCTUATION.sub("", normalized)


class HomeAssistantActionError(RuntimeError):
    """Raised when a configured Home Assistant action fails."""


@dataclass(frozen=True)
class OfflineAction:
    domain: str
    service: str
    service_data: dict[str, Any]
    response: str


class OfflineCommandRouter:
    def __init__(
        self,
        *,
        enabled: bool,
        home_assistant_url: str = "",
        home_assistant_token: str = "",
        timeout_seconds: float = 15,
        error_response: str = (
            "Home Assistant could not execute that command."
        ),
        commands: dict[str, OfflineAction] | None = None,
    ) -> None:
        self.enabled = enabled
        self.home_assistant_url = home_assistant_url.rstrip("/")
        self.home_assistant_token = home_assistant_token
        self.timeout_seconds = timeout_seconds
        self.error_response = error_response
        self.commands = commands or {}

    @classmethod
    def disabled(cls) -> "OfflineCommandRouter":
        return cls(enabled=False)

    @classmethod
    def from_environment(cls) -> "OfflineCommandRouter":
        configured_path = os.getenv(
            "HA_OFFLINE_CONFIG_PATH",
            "/config/offline_commands.json",
        ).strip()

        if not configured_path:
            logger.info("Offline Home Assistant routing is disabled")
            return cls.disabled()

        path = Path(configured_path)
        if not path.is_file():
            logger.info(
                "Offline Home Assistant configuration not found at %s; "
                "routing is disabled",
                path,
            )
            return cls.disabled()

        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError(
                "Invalid offline command configuration"
            ) from error

        if raw.get("HA_OFFLINE_EN") is not True:
            logger.info("Offline Home Assistant routing is disabled")
            return cls.disabled()

        home_assistant = raw.get("home_assistant")
        if not isinstance(home_assistant, dict):
            raise RuntimeError(
                "home_assistant must be an object"
            )

        url = str(home_assistant.get("url", "")).strip()
        if not url.startswith(("http://", "https://")):
            raise RuntimeError(
                "home_assistant.url must be an HTTP or HTTPS URL"
            )

        token = os.getenv("HOME_ASSISTANT_TOKEN", "").strip()
        if not token:
            raise RuntimeError(
                "HOME_ASSISTANT_TOKEN is required when HA_OFFLINE_EN is true"
            )

        try:
            timeout_seconds = float(
                home_assistant.get("timeout_seconds", 15)
            )
        except (TypeError, ValueError) as error:
            raise RuntimeError(
                "home_assistant.timeout_seconds must be a number"
            ) from error

        if timeout_seconds <= 0:
            raise RuntimeError(
                "home_assistant.timeout_seconds must be positive"
            )

        commands_value = raw.get("commands")
        if not isinstance(commands_value, dict) or not commands_value:
            raise RuntimeError(
                "commands must be a non-empty object"
            )

        commands: dict[str, OfflineAction] = {}
        for phrase, value in commands_value.items():
            if not isinstance(phrase, str) or not isinstance(value, dict):
                raise RuntimeError(
                    "Every offline command must map a phrase to an object"
                )

            normalized = normalize_offline_command(phrase)
            if not normalized:
                raise RuntimeError("Offline command phrases cannot be empty")
            if normalized in commands:
                raise RuntimeError(
                    "Duplicate normalized offline command: "
                    f"{normalized}"
                )

            domain = str(value.get("domain", "")).strip()
            service = str(value.get("service", "")).strip()
            if not NAME_PATTERN.fullmatch(domain):
                raise RuntimeError(
                    f"Invalid Home Assistant domain for command: {phrase}"
                )
            if not NAME_PATTERN.fullmatch(service):
                raise RuntimeError(
                    f"Invalid Home Assistant service for command: {phrase}"
                )

            service_data = value.get("service_data", {})
            if not isinstance(service_data, dict):
                raise RuntimeError(
                    f"service_data must be an object for command: {phrase}"
                )

            response = str(value.get("response", "Done.")).strip()
            if not response:
                raise RuntimeError(
                    f"response cannot be empty for command: {phrase}"
                )

            commands[normalized] = OfflineAction(
                domain=domain,
                service=service,
                service_data=service_data,
                response=response,
            )

        logger.info(
            "Offline Home Assistant routing enabled with %d commands",
            len(commands),
        )
        return cls(
            enabled=True,
            home_assistant_url=url,
            home_assistant_token=token,
            timeout_seconds=timeout_seconds,
            error_response=str(
                home_assistant.get(
                    "error_response",
                    "Home Assistant could not execute that command.",
                )
            ),
            commands=commands,
        )

    async def execute_if_matched(self, text: str) -> str | None:
        if not self.enabled:
            return None

        normalized = normalize_offline_command(text)
        action = self.commands.get(normalized)
        if action is None:
            return None

        logger.info(
            "Offline Home Assistant command matched: %s",
            normalized,
        )
        await self._call_home_assistant(action)
        return action.response

    async def _call_home_assistant(
        self,
        action: OfflineAction,
    ) -> None:
        url = (
            f"{self.home_assistant_url}/api/services/"
            f"{action.domain}/{action.service}"
        )
        headers = {
            "Authorization": f"Bearer {self.home_assistant_token}",
            "Content-Type": "application/json",
        }

        safe_to_retry = (
            action.domain in {"light", "switch"}
            and action.service in {"turn_on", "turn_off"}
        )
        attempts = 2 if safe_to_retry else 1

        async with httpx.AsyncClient(
            timeout=self.timeout_seconds,
            follow_redirects=False,
        ) as client:
            for attempt in range(1, attempts + 1):
                try:
                    response = await client.post(
                        url,
                        headers=headers,
                        json=action.service_data,
                    )
                except httpx.HTTPError as error:
                    if attempt < attempts:
                        await asyncio.sleep(0.4)
                        continue
                    raise HomeAssistantActionError(
                        f"Home Assistant request failed: {error}"
                    ) from error

                if response.is_success:
                    return

                if response.status_code >= 500 and attempt < attempts:
                    await asyncio.sleep(0.4)
                    continue

                body = response.text.strip().replace("\n", " ")[:500]
                detail = f": {body}" if body else ""
                raise HomeAssistantActionError(
                    "Home Assistant returned HTTP "
                    f"{response.status_code}{detail}"
                )

