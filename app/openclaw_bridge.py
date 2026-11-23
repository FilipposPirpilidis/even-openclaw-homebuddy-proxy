import asyncio
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from app.config import Settings


logger = logging.getLogger(__name__)


class OpenClawBridgeUnavailable(RuntimeError):
    """Raised when the OpenClaw Gateway cannot serve a request."""


class OpenClawBridgeTimeout(TimeoutError):
    """Raised when an OpenClaw agent turn exceeds the configured timeout."""


class OpenClawBridge:
    def __init__(
        self,
        settings: Settings,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self._transport = transport
        self._client: httpx.AsyncClient | None = None
        self._lock = asyncio.Lock()
        self._session_user = settings.openclaw_session_user

    @property
    def ready(self) -> bool:
        return self._client is not None

    @property
    def session_user(self) -> str:
        return self._session_user

    async def start(self) -> None:
        self._session_user = self._load_session_user()
        self._client = httpx.AsyncClient(
            base_url=self.settings.openclaw_base_url,
            headers={
                "Authorization": (
                    f"Bearer {self.settings.openclaw_gateway_token}"
                ),
                "Content-Type": "application/json",
            },
            timeout=self.settings.openclaw_timeout_seconds,
            transport=self._transport,
        )
        logger.info(
            "Direct OpenClaw bridge ready base_url=%s agent=%s",
            self.settings.openclaw_base_url,
            self.settings.openclaw_agent,
        )

    async def stop(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def ask(
        self,
        text: str,
        *,
        system_context: str | None = None,
    ) -> str:
        if self._client is None:
            raise OpenClawBridgeUnavailable(
                "Direct OpenClaw bridge is not ready"
            )

        async with self._lock:
            return await self._ask_locked(text, system_context=system_context)

    async def reset_session(self) -> None:
        """Rotate and persist the identity used to derive OpenClaw context."""
        async with self._lock:
            session_user = (
                f"{self.settings.openclaw_session_user}:{uuid.uuid4()}"
            )
            try:
                self._store_session_user(session_user)
            except OSError as error:
                raise OpenClawBridgeUnavailable(
                    f"Could not persist the new OpenClaw session: {error}"
                ) from error
            self._session_user = session_user
            logger.info("Direct OpenClaw conversation session rotated")

    async def _ask_locked(
        self,
        text: str,
        *,
        system_context: str | None,
    ) -> str:
        assert self._client is not None

        messages: list[dict[str, str]] = []
        if system_context:
            messages.append(
                {
                    "role": "system",
                    "content": system_context,
                }
            )
        messages.append(
            {
                "role": "user",
                "content": text,
            }
        )

        try:
            response = await self._client.post(
                "/v1/chat/completions",
                json={
                    "model": self.settings.openclaw_agent,
                    "messages": messages,
                    "user": self._session_user,
                    "stream": False,
                },
            )
            response.raise_for_status()
        except httpx.TimeoutException as error:
            raise OpenClawBridgeTimeout(
                "OpenClaw agent response timed out"
            ) from error
        except httpx.HTTPStatusError as error:
            detail = error.response.text.strip().replace("\n", " ")[:500]
            raise OpenClawBridgeUnavailable(
                "OpenClaw Gateway returned HTTP "
                f"{error.response.status_code}: {detail}"
            ) from error
        except httpx.RequestError as error:
            raise OpenClawBridgeUnavailable(
                f"OpenClaw Gateway request failed: {error}"
            ) from error

        try:
            payload = response.json()
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, ValueError) as error:
            raise OpenClawBridgeUnavailable(
                "OpenClaw Gateway returned an invalid completion"
            ) from error

        answer = _completion_text(content)
        if not answer:
            raise OpenClawBridgeUnavailable(
                "OpenClaw Gateway returned an empty completion"
            )
        return answer

    def _load_session_user(self) -> str:
        path = Path(self.settings.openclaw_session_state_path)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return self.settings.openclaw_session_user
        except (OSError, json.JSONDecodeError) as error:
            logger.warning(
                "Ignoring unreadable OpenClaw session state path=%s error=%s",
                path,
                error,
            )
            return self.settings.openclaw_session_user

        if not isinstance(payload, dict):
            return self.settings.openclaw_session_user
        if payload.get("base") != self.settings.openclaw_session_user:
            return self.settings.openclaw_session_user
        session_user = payload.get("user")
        if not isinstance(session_user, str) or not session_user.strip():
            return self.settings.openclaw_session_user
        if len(session_user) > 256:
            return self.settings.openclaw_session_user
        return session_user

    def _store_session_user(self, session_user: str) -> None:
        path = Path(self.settings.openclaw_session_state_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.tmp")
        temporary.write_text(
            json.dumps(
                {
                    "version": 1,
                    "base": self.settings.openclaw_session_user,
                    "user": session_user,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)


def _completion_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""

    parts: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        if item.get("type") not in {"text", "output_text"}:
            continue
        text = item.get("text")
        if isinstance(text, str) and text.strip():
            parts.append(text.strip())
    return "\n".join(parts)
