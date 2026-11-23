import hmac
import logging
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.config import get_settings
from app.location_store import LocationStore
from app.openclaw_bridge import (
    OpenClawBridge,
    OpenClawBridgeTimeout,
    OpenClawBridgeUnavailable,
)
from app.offline_commands import (
    HomeAssistantActionError,
    OfflineCommandRouter,
)
from app.session_commands import session_reset_response
from app.telegram_bridge import (
    TelegramBridge,
    TelegramBridgeTimeout,
    TelegramBridgeUnavailable,
)


settings = get_settings()

logging.basicConfig(
    level=getattr(logging, settings.log_level, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

bridge: TelegramBridge | OpenClawBridge
if settings.openclaw_transport == "direct":
    bridge = OpenClawBridge(settings)
else:
    bridge = TelegramBridge(settings)
offline_router = OfflineCommandRouter.from_environment()
location_store = LocationStore(
    settings.location_state_path,
    settings.location_max_age_seconds,
)


class LocationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    accuracy_meters: float | None = Field(default=None, ge=0)
    captured_at: datetime | None = None

    @model_validator(mode="after")
    def require_enabled_coordinates(self) -> "LocationUpdate":
        if self.enabled and (self.latitude is None or self.longitude is None):
            raise ValueError(
                "latitude and longitude are required when location is enabled"
            )
        if self.captured_at is not None and self.captured_at.tzinfo is None:
            raise ValueError("captured_at must include a timezone")
        return self


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="allow")

    role: str
    content: Any


class ChatCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str = "openclaw"
    messages: list[ChatMessage]
    stream: bool = False
    location: LocationUpdate | None = None


def extract_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") in {"text", "input_text"}:
                value = item.get("text", "")
                if isinstance(value, str) and value.strip():
                    parts.append(value.strip())
        return "\n".join(parts)
    return ""


def latest_user_text(request: ChatCompletionRequest) -> str:
    for message in reversed(request.messages):
        if message.role == "user":
            return extract_text(message.content)
    return ""


def completion(answer: str, model: str | None = None) -> dict[str, Any]:
    return {
        "id": f"chatcmpl_{uuid.uuid4()}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model or f"{settings.openclaw_transport}/openclaw",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": answer,
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        },
    }


def authorize(authorization: str | None) -> None:
    prefix = "Bearer "
    if not authorization or not authorization.startswith(prefix):
        raise HTTPException(status_code=401, detail="Missing bearer token")

    supplied = authorization[len(prefix) :]
    if not hmac.compare_digest(supplied, settings.proxy_bearer_token):
        raise HTTPException(status_code=401, detail="Invalid bearer token")


def require_location_context() -> None:
    if not settings.location_context_enabled:
        raise HTTPException(
            status_code=409,
            detail="Location context is disabled on this proxy",
        )
    if (
        settings.openclaw_transport != "direct"
        or not isinstance(bridge, OpenClawBridge)
    ):
        raise HTTPException(
            status_code=409,
            detail="Location context is available only in direct mode",
        )


async def apply_location_update(update: LocationUpdate) -> dict[str, object]:
    require_location_context()
    if not update.enabled:
        await location_store.clear()
        return {"enabled": False, "location": None}

    assert update.latitude is not None
    assert update.longitude is not None
    try:
        await location_store.update(
            latitude=update.latitude,
            longitude=update.longitude,
            captured_at=update.captured_at or datetime.now(timezone.utc),
            accuracy_meters=update.accuracy_meters,
        )
    except OSError as error:
        logger.error("Could not persist user-shared location: %s", error)
        raise HTTPException(
            status_code=503,
            detail="Could not persist location",
        ) from error
    return await location_store.status()


@asynccontextmanager
async def lifespan(_: FastAPI):
    await bridge.start()
    try:
        yield
    finally:
        await bridge.stop()


app = FastAPI(
    title="Even OpenClaw HomeBuddy Proxy",
    lifespan=lifespan,
)


@app.get("/")
async def service_information() -> dict[str, Any]:
    return {
        "service": "even-openclaw-homebuddy-proxy",
        "status": "live" if bridge.ready else "starting",
        "transport": settings.openclaw_transport,
        "openai_endpoint": "/v1/chat/completions",
    }


@app.get("/healthz")
async def health() -> dict[str, Any]:
    if not bridge.ready:
        raise HTTPException(
            status_code=503,
            detail=f"{settings.openclaw_transport} transport is not ready",
        )
    return {
        "ok": True,
        "status": "live",
        "transport": settings.openclaw_transport,
        "openclaw_agent": settings.openclaw_agent,
        "telegram_target_id": (
            settings.telegram_target_id
            if settings.openclaw_transport == "telegram"
            else None
        ),
        "home_assistant_offline_commands": offline_router.enabled,
        "offline_command_count": len(offline_router.commands),
        "location_context_enabled": settings.location_context_enabled,
        "last_location_available": location_store.available,
    }


@app.get("/models")
@app.get("/v1/models")
async def models(
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    authorize(authorization)
    return {
        "object": "list",
        "data": [
            {
                "id": "openclaw",
                "object": "model",
                "created": 0,
                "owned_by": "local",
            }
        ],
    }


@app.post("/")
@app.post("/chat/completions")
@app.post("/v1/chat/completions")
async def chat_completions(
    request: ChatCompletionRequest,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    authorize(authorization)
    if request.stream:
        raise HTTPException(
            status_code=400,
            detail="Streaming responses are not supported",
        )

    text = latest_user_text(request)
    if not text:
        raise HTTPException(status_code=400, detail="No user message found")
    if len(text) > settings.max_request_chars:
        raise HTTPException(status_code=413, detail="User message is too long")

    if request.location is not None:
        await apply_location_update(request.location)

    reset_response = session_reset_response(text)
    if (
        reset_response is not None
        and settings.openclaw_transport == "direct"
        and isinstance(bridge, OpenClawBridge)
    ):
        try:
            await bridge.reset_session()
        except OpenClawBridgeUnavailable as error:
            logger.error("OpenClaw session reset failed: %s", error)
            raise HTTPException(
                status_code=503,
                detail="OpenClaw session reset failed",
            ) from error
        return completion(reset_response)

    try:
        offline_answer = await offline_router.execute_if_matched(text)
    except HomeAssistantActionError as error:
        logger.error("Offline Home Assistant action failed: %s", error)
        return completion(offline_router.error_response)

    if offline_answer is not None:
        return completion(offline_answer)

    try:
        if isinstance(bridge, OpenClawBridge):
            system_context = (
                await location_store.context_message()
                if settings.location_context_enabled
                else None
            )
            answer = await bridge.ask(
                text,
                system_context=system_context,
            )
        else:
            answer = await bridge.ask(text)
    except (TelegramBridgeTimeout, OpenClawBridgeTimeout) as error:
        logger.error("Upstream response timed out: %s", error)
        raise HTTPException(
            status_code=504,
            detail="OpenClaw response timed out",
        ) from error
    except (TelegramBridgeUnavailable, OpenClawBridgeUnavailable) as error:
        logger.error("OpenClaw transport unavailable: %s", error)
        raise HTTPException(
            status_code=503,
            detail="OpenClaw transport is unavailable",
        ) from error

    return completion(answer)


@app.get("/v1/location")
async def location_status(
    authorization: str | None = Header(default=None),
) -> dict[str, object]:
    authorize(authorization)
    require_location_context()
    return await location_store.status()


@app.post("/v1/location")
async def update_location(
    update: LocationUpdate,
    authorization: str | None = Header(default=None),
) -> dict[str, object]:
    authorize(authorization)
    return await apply_location_update(update)


@app.delete("/v1/location")
async def clear_location(
    authorization: str | None = Header(default=None),
) -> dict[str, object]:
    authorize(authorization)
    require_location_context()
    await location_store.clear()
    return {"enabled": False, "location": None}


@app.post("/v1/session/reset")
async def reset_session(
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    authorize(authorization)
    if (
        settings.openclaw_transport != "direct"
        or not isinstance(bridge, OpenClawBridge)
    ):
        raise HTTPException(
            status_code=409,
            detail="Proxy-managed reset is available only in direct mode",
        )
    try:
        await bridge.reset_session()
    except OpenClawBridgeUnavailable as error:
        logger.error("OpenClaw session reset failed: %s", error)
        raise HTTPException(
            status_code=503,
            detail="OpenClaw session reset failed",
        ) from error
    return {
        "reset": True,
        "message": "New conversation started.",
    }

