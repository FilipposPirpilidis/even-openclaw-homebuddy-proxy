import hmac
import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict

from app.config import get_settings
from app.offline_commands import (
    HomeAssistantActionError,
    OfflineCommandRouter,
)
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

bridge = TelegramBridge(settings)
offline_router = OfflineCommandRouter.from_environment()


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="allow")

    role: str
    content: Any


class ChatCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str = "openclaw"
    messages: list[ChatMessage]
    stream: bool = False


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


def completion(answer: str) -> dict[str, Any]:
    return {
        "id": f"chatcmpl_{uuid.uuid4()}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": "telegram/openclaw",
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


@asynccontextmanager
async def lifespan(_: FastAPI):
    await bridge.start()
    try:
        yield
    finally:
        await bridge.stop()


app = FastAPI(
    title="Even OpenClaw Telegram Proxy",
    lifespan=lifespan,
)


@app.get("/")
async def service_information() -> dict[str, Any]:
    return {
        "service": "even-openclaw-homebuddy-proxy",
        "status": "live" if bridge.ready else "starting",
        "openai_endpoint": "/v1/chat/completions",
    }


@app.get("/healthz")
async def health() -> dict[str, Any]:
    if not bridge.ready:
        raise HTTPException(status_code=503, detail="Telegram is not ready")
    return {
        "ok": True,
        "status": "live",
        "telegram_target_id": settings.telegram_target_id,
        "home_assistant_offline_commands": offline_router.enabled,
        "offline_command_count": len(offline_router.commands),
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

    try:
        offline_answer = await offline_router.execute_if_matched(text)
    except HomeAssistantActionError as error:
        logger.error("Offline Home Assistant action failed: %s", error)
        return completion(offline_router.error_response)

    if offline_answer is not None:
        return completion(offline_answer)

    try:
        answer = await bridge.ask(text)
    except TelegramBridgeTimeout as error:
        logger.error("Telegram response timed out: %s", error)
        raise HTTPException(
            status_code=504,
            detail="Telegram response timed out",
        ) from error
    except TelegramBridgeUnavailable as error:
        logger.error("Telegram bridge unavailable: %s", error)
        raise HTTPException(
            status_code=503,
            detail="Telegram bridge is unavailable",
        ) from error

    return completion(answer)

