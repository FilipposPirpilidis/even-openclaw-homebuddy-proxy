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


def normalize_intent_text(text: str) -> str:
    """Normalize natural-language command text into comparable words."""
    normalized = unicodedata.normalize("NFKD", text).casefold()
    characters: list[str] = []
    for character in normalized:
        if unicodedata.combining(character):
            continue
        if character.isalnum() or character == "_":
            characters.append(character)
        else:
            characters.append(" ")
    return " ".join("".join(characters).split())


class HomeAssistantActionError(RuntimeError):
    """Raised when a configured Home Assistant action fails."""


@dataclass(frozen=True)
class OfflineAction:
    domain: str
    service: str
    service_data: dict[str, Any]
    response: str


@dataclass(frozen=True)
class IntentEntity:
    aliases: tuple[tuple[str, ...], ...]
    actions: dict[str, OfflineAction]


class IntentMatcher:
    """Match a complete utterance against a small, explicit grammar."""

    def __init__(
        self,
        *,
        actions: dict[tuple[str, ...], str],
        entities: dict[str, IntentEntity],
        articles: set[tuple[str, ...]],
        polite_phrases: set[tuple[str, ...]],
    ) -> None:
        self.actions = actions
        self.entities = entities
        self.articles = articles
        self.polite_phrases = polite_phrases

    @staticmethod
    def _words(value: str) -> tuple[str, ...]:
        return tuple(normalize_intent_text(value).split())

    @classmethod
    def from_config(cls, value: Any) -> "IntentMatcher | None":
        if value is None:
            return None
        if not isinstance(value, dict):
            raise RuntimeError("intent_matching must be an object")
        if value.get("enabled") is not True:
            return None

        actions_value = value.get("actions")
        entities_value = value.get("entities")
        if not isinstance(actions_value, dict) or not actions_value:
            raise RuntimeError(
                "intent_matching.actions must be a non-empty object"
            )
        if not isinstance(entities_value, dict) or not entities_value:
            raise RuntimeError(
                "intent_matching.entities must be a non-empty object"
            )

        actions: dict[tuple[str, ...], str] = {}
        for action_name, phrases in actions_value.items():
            if not NAME_PATTERN.fullmatch(str(action_name)):
                raise RuntimeError(f"Invalid intent action name: {action_name}")
            if not isinstance(phrases, list) or not phrases:
                raise RuntimeError(
                    f"Intent action {action_name} must contain phrases"
                )
            for phrase in phrases:
                words = cls._words(str(phrase))
                if not words:
                    raise RuntimeError("Intent action phrases cannot be empty")
                previous = actions.get(words)
                if previous is not None and previous != action_name:
                    raise RuntimeError(
                        f"Duplicate intent action phrase: {' '.join(words)}"
                    )
                actions[words] = str(action_name)

        articles = cls._phrase_set(value.get("articles", []), "articles")
        polite_phrases = cls._phrase_set(
            value.get("polite_phrases", []),
            "polite_phrases",
        )

        entities: dict[str, IntentEntity] = {}
        seen_aliases: dict[tuple[str, ...], str] = {}
        for entity_name, entity_value in entities_value.items():
            if not NAME_PATTERN.fullmatch(str(entity_name)):
                raise RuntimeError(f"Invalid intent entity name: {entity_name}")
            if not isinstance(entity_value, dict):
                raise RuntimeError(
                    f"Intent entity {entity_name} must be an object"
                )
            aliases_value = entity_value.get("aliases")
            if not isinstance(aliases_value, list) or not aliases_value:
                raise RuntimeError(f"Intent entity {entity_name} needs aliases")

            aliases: list[tuple[str, ...]] = []
            for alias in aliases_value:
                words = cls._words(str(alias))
                if not words:
                    raise RuntimeError("Intent entity aliases cannot be empty")
                previous = seen_aliases.get(words)
                if previous is not None and previous != entity_name:
                    raise RuntimeError(
                        f"Duplicate intent entity alias: {' '.join(words)}"
                    )
                seen_aliases[words] = str(entity_name)
                aliases.append(words)

            entity_actions_value = entity_value.get("actions")
            if (
                not isinstance(entity_actions_value, dict)
                or not entity_actions_value
            ):
                raise RuntimeError(f"Intent entity {entity_name} needs actions")
            entity_actions: dict[str, OfflineAction] = {}
            for action_name, action_value in entity_actions_value.items():
                if action_name not in actions.values():
                    raise RuntimeError(
                        f"Unknown action {action_name} for intent entity {entity_name}"
                    )
                entity_actions[str(action_name)] = _parse_action(
                    action_value,
                    f"intent {entity_name}.{action_name}",
                )

            entities[str(entity_name)] = IntentEntity(
                aliases=tuple(aliases),
                actions=entity_actions,
            )

        return cls(
            actions=actions,
            entities=entities,
            articles=articles,
            polite_phrases=polite_phrases,
        )

    @classmethod
    def _phrase_set(cls, value: Any, field: str) -> set[tuple[str, ...]]:
        if not isinstance(value, list):
            raise RuntimeError(f"intent_matching.{field} must be an array")
        result: set[tuple[str, ...]] = set()
        for phrase in value:
            words = cls._words(str(phrase))
            if not words:
                raise RuntimeError(f"intent_matching.{field} cannot contain blanks")
            result.add(words)
        return result

    @staticmethod
    def _remove_phrase(
        words: tuple[str, ...],
        phrase: tuple[str, ...],
    ) -> tuple[str, ...]:
        if not phrase or len(phrase) > len(words):
            return words
        result: list[str] = []
        index = 0
        while index < len(words):
            if words[index : index + len(phrase)] == phrase:
                index += len(phrase)
            else:
                result.append(words[index])
                index += 1
        return tuple(result)

    def match(self, text: str) -> OfflineAction | None:
        words = self._words(text)
        if not words:
            return None

        # Courtesy words may appear before, after, or between the action and
        # entity. Removing only explicitly configured phrases keeps the rest
        # of the utterance subject to full-sentence matching.
        for phrase in sorted(self.polite_phrases, key=len, reverse=True):
            words = self._remove_phrase(words, phrase)

        matches: list[OfflineAction] = []
        for action_phrase, action_name in self.actions.items():
            if words[: len(action_phrase)] != action_phrase:
                continue
            remainder = words[len(action_phrase) :]
            for article in sorted(self.articles, key=len, reverse=True):
                if remainder[: len(article)] == article:
                    remainder = remainder[len(article) :]
                    break
            for entity in self.entities.values():
                if remainder not in entity.aliases:
                    continue
                action = entity.actions.get(action_name)
                if action is not None:
                    matches.append(action)

        if len(matches) == 1:
            return matches[0]
        return None


def _parse_action(value: Any, label: str) -> OfflineAction:
    if not isinstance(value, dict):
        raise RuntimeError(f"Action configuration must be an object: {label}")
    domain = str(value.get("domain", "")).strip()
    service = str(value.get("service", "")).strip()
    if not NAME_PATTERN.fullmatch(domain):
        raise RuntimeError(f"Invalid Home Assistant domain for {label}")
    if not NAME_PATTERN.fullmatch(service):
        raise RuntimeError(f"Invalid Home Assistant service for {label}")
    service_data = value.get("service_data", {})
    if not isinstance(service_data, dict):
        raise RuntimeError(f"service_data must be an object for {label}")
    response = str(value.get("response", "Done.")).strip()
    if not response:
        raise RuntimeError(f"response cannot be empty for {label}")
    return OfflineAction(
        domain=domain,
        service=service,
        service_data=service_data,
        response=response,
    )


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
        intent_matcher: IntentMatcher | None = None,
    ) -> None:
        self.enabled = enabled
        self.home_assistant_url = home_assistant_url.rstrip("/")
        self.home_assistant_token = home_assistant_token
        self.timeout_seconds = timeout_seconds
        self.error_response = error_response
        self.commands = commands or {}
        self.intent_matcher = intent_matcher

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

            commands[normalized] = _parse_action(
                value,
                f"command: {phrase}",
            )

        intent_matcher = IntentMatcher.from_config(raw.get("intent_matching"))

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
            intent_matcher=intent_matcher,
        )

    async def execute_if_matched(self, text: str) -> str | None:
        if not self.enabled:
            return None

        normalized = normalize_offline_command(text)
        action = self.commands.get(normalized)
        match_type = "exact"
        if action is None and self.intent_matcher is not None:
            action = self.intent_matcher.match(text)
            match_type = "intent"
        if action is None:
            return None

        logger.info(
            "Offline Home Assistant command matched (%s): %s",
            match_type,
            normalized,
        )
        await self._call_home_assistant(action)
        return action.response

    async def execute_if_match(self, text: str) -> str | None:
        """Compatibility alias for installations using the older API name."""
        return await self.execute_if_matched(text)

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

