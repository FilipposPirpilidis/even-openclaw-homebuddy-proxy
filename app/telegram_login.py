import asyncio
import os

from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError


def required_environment(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


async def main() -> None:
    api_id = int(required_environment("TELEGRAM_API_ID"))
    api_hash = required_environment("TELEGRAM_API_HASH")
    session_path = os.getenv(
        "TELEGRAM_SESSION_PATH",
        "/data/telegram-user",
    )

    client = TelegramClient(session_path, api_id, api_hash)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            phone = input(
                "Telegram phone number including country code: "
            ).strip()
            sent_code = await client.send_code_request(phone)
            code = input("Telegram login code: ").strip()
            try:
                await client.sign_in(
                    phone=phone,
                    code=code,
                    phone_code_hash=sent_code.phone_code_hash,
                )
            except SessionPasswordNeededError:
                password = input("Telegram two-step password: ")
                await client.sign_in(password=password)

        me = await client.get_me()
        print(f"Authorized Telegram user id: {me.id}")

        target_value = os.getenv("TELEGRAM_TARGET", "").strip()
        if not target_value:
            target_value = input(
                "Telegram bot username or numeric id: "
            ).strip()

        target: int | str
        try:
            target = int(target_value)
        except ValueError:
            target = target_value

        entity = await client.get_entity(target)
        print(f"Resolved Telegram target id: {entity.id}")
        print(
            "Save this value as TELEGRAM_TARGET_ID in your .env file."
        )
    finally:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())

