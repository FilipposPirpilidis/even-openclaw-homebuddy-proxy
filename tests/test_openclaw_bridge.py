import asyncio
import json

import httpx
import pytest
from pydantic import ValidationError

from app.config import Settings
from app.openclaw_bridge import (
    OpenClawBridge,
    OpenClawBridgeUnavailable,
)


def direct_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "openclaw_transport": "direct",
        "openclaw_base_url": "http://openclaw-gateway:18789/",
        "openclaw_gateway_token": "g" * 32,
        "openclaw_agent": "openclaw/glasses",
        "openclaw_session_user": "even-test-user",
        "proxy_bearer_token": "p" * 32,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_direct_bridge_sends_stable_openclaw_session() -> None:
    observed: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        observed["path"] = request.url.path
        observed["authorization"] = request.headers.get("authorization")
        observed["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "Direct response",
                        }
                    }
                ]
            },
        )

    async def scenario() -> str:
        bridge = OpenClawBridge(
            direct_settings(),
            transport=httpx.MockTransport(handler),
        )
        await bridge.start()
        try:
            return await bridge.ask("Hello from the glasses")
        finally:
            await bridge.stop()

    assert asyncio.run(scenario()) == "Direct response"
    assert observed["path"] == "/v1/chat/completions"
    assert observed["authorization"] == f"Bearer {'g' * 32}"
    assert observed["payload"] == {
        "model": "openclaw/glasses",
        "messages": [
            {
                "role": "user",
                "content": "Hello from the glasses",
            }
        ],
        "user": "even-test-user",
        "stream": False,
    }


def test_direct_bridge_rejects_upstream_http_error() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "unauthorized"})

    async def scenario() -> None:
        bridge = OpenClawBridge(
            direct_settings(),
            transport=httpx.MockTransport(handler),
        )
        await bridge.start()
        try:
            with pytest.raises(
                OpenClawBridgeUnavailable,
                match="HTTP 401",
            ):
                await bridge.ask("Hello")
        finally:
            await bridge.stop()

    asyncio.run(scenario())


def test_direct_mode_requires_gateway_token() -> None:
    with pytest.raises(
        ValidationError,
        match="OPENCLAW_GATEWAY_TOKEN",
    ):
        direct_settings(openclaw_gateway_token="")
