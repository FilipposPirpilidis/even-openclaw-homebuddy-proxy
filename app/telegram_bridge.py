import asyncio
import logging
import time
from typing import Any

from telethon import TelegramClient, events

from app.config import Settings


logger = logging.getLogger(__name__)


class TelegramBridgeUnavailable(RuntimeError):
    """Raised when the Telegram user session cannot be used."""


class TelegramBridgeTimeout(TimeoutError):
    """Raised when the target does not return a stable response."""


def is_transient_status(text: str) -> bool:
    normalized = " ".join(text.casefold().split())
    lines = [
        line.strip().casefold()
        for line in text.splitlines()
        if line.strip()
    ]

    if normalized in {"working", "thinking", "checking", "searching"}:
        return True
    if normalized.startswith("last activity:"):
        return True
    if lines and lines[0] in {
        "working",
        "thinking",
        "checking",
        "searching",
    }:
        return True
    if len(normalized) < 300 and any(
        line.endswith(": running") for line in lines
    ):
        return True
    return False


class TelegramBridge:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = TelegramClient(
            settings.telegram_session_path,
            settings.telegram_api_id,
            settings.telegram_api_hash,
            sequential_updates=True,
        )
        self.target: Any | None = None
        self.lock = asyncio.Lock()

    @property
    def ready(self) -> bool:
        return self.client.is_connected() and self.target is not None

    async def start(self) -> None:
        if self.settings.telegram_target_id <= 0:
            raise TelegramBridgeUnavailable(
                "TELEGRAM_TARGET_ID must be set to the Telegram bot ID"
            )

        await self.client.connect()
        if not await self.client.is_user_authorized():
            raise TelegramBridgeUnavailable(
                "Telegram session is not authorized; run telegram-login"
            )

        self.target = await self.client.get_entity(
            self.settings.telegram_target_id
        )
        logger.info(
            "Telegram bridge ready for locked target id=%d",
            self.settings.telegram_target_id,
        )

    async def stop(self) -> None:
        if self.client.is_connected():
            await self.client.disconnect()

    async def ask(self, text: str) -> str:
        if not self.ready:
            raise TelegramBridgeUnavailable("Telegram bridge is not ready")

        async with self.lock:
            return await self._ask_locked(text)

    async def _ask_locked(self, text: str) -> str:
        queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()
        sent_message_id: int | None = None

        async def on_message(event: Any) -> None:
            message = event.message
            if getattr(message, "out", False):
                return
            if sent_message_id is None or message.id <= sent_message_id:
                return

            reply_to_id = getattr(message, "reply_to_msg_id", None)
            if reply_to_id not in (None, sent_message_id):
                return
            await queue.put(("message", message))

        async def on_deleted(event: Any) -> None:
            await queue.put(("deleted", set(event.deleted_ids)))

        new_message_event = events.NewMessage(
            chats=self.settings.telegram_target_id,
            incoming=True,
        )
        edited_message_event = events.MessageEdited(
            chats=self.settings.telegram_target_id,
            incoming=True,
        )
        deleted_message_event = events.MessageDeleted(
            chats=self.settings.telegram_target_id,
        )

        self.client.add_event_handler(on_message, new_message_event)
        self.client.add_event_handler(on_message, edited_message_event)
        self.client.add_event_handler(on_deleted, deleted_message_event)

        try:
            sent = await self.client.send_message(self.target, text)
            sent_message_id = sent.id
            logger.info(
                "Telegram request sent message_id=%d",
                sent_message_id,
            )

            deadline = (
                time.monotonic()
                + self.settings.telegram_response_timeout_seconds
            )
            settle_deadline: float | None = None
            stable_messages: dict[int, str] = {}

            while True:
                now = time.monotonic()
                if settle_deadline is not None and now >= settle_deadline:
                    break

                next_deadline = deadline
                if settle_deadline is not None:
                    next_deadline = min(next_deadline, settle_deadline)

                remaining = next_deadline - now
                if remaining <= 0:
                    if stable_messages:
                        break
                    raise TelegramBridgeTimeout(
                        "Telegram target did not return a stable response"
                    )

                try:
                    kind, value = await asyncio.wait_for(
                        queue.get(),
                        timeout=remaining,
                    )
                except TimeoutError:
                    if stable_messages:
                        break
                    raise TelegramBridgeTimeout(
                        "Telegram target did not return a stable response"
                    )

                if kind == "deleted":
                    for message_id in value:
                        stable_messages.pop(message_id, None)
                    continue

                message = value
                message_text = (message.raw_text or "").strip()
                if not message_text:
                    continue

                transient = is_transient_status(message_text)
                logger.info(
                    "Telegram response event message_id=%d transient=%s",
                    message.id,
                    transient,
                )
                if transient:
                    stable_messages.pop(message.id, None)
                    continue

                stable_messages[message.id] = message_text
                settle_deadline = (
                    time.monotonic()
                    + self.settings.telegram_settle_seconds
                )

            if not stable_messages:
                raise TelegramBridgeTimeout(
                    "Telegram target returned no stable final response"
                )

            answer = "\n\n".join(
                stable_messages[message_id]
                for message_id in sorted(stable_messages)
            )
            logger.info(
                "Telegram final response correlated request_id=%d "
                "response_messages=%d",
                sent_message_id,
                len(stable_messages),
            )
            return answer
        finally:
            self.client.remove_event_handler(on_message, new_message_event)
            self.client.remove_event_handler(
                on_message,
                edited_message_event,
            )
            self.client.remove_event_handler(
                on_deleted,
                deleted_message_event,
            )

