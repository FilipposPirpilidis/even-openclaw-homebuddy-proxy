import asyncio
import logging
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

    @property
    def ready(self) -> bool:
        return self._client is not None

    async def start(self) -> None:
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

    async def ask(self, text: str) -> str:
        if self._client is None:
            raise OpenClawBridgeUnavailable(
                "Direct OpenClaw bridge is not ready"
            )

        async with self._lock:
            return await self._ask_locked(text)

    async def _ask_locked(self, text: str) -> str:
        assert self._client is not None

        try:
            response = await self._client.post(
                "/v1/chat/completions",
                json={
                    "model": self.settings.openclaw_agent,
                    "messages": [
                        {
                            "role": "user",
                            "content": text,
                        }
                    ],
                    "user": self.settings.openclaw_session_user,
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
