import asyncio
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError, UserAlreadyParticipantError
from telethon.tl.functions.channels import JoinChannelRequest


BOT_USERNAME = "Sibuwb_bot"
CHANNEL_USERNAME = "swbnews"
SESSION_NAME = "sibuwb_session"
DEFAULT_LIMIT = 50
OUTPUT_FILE = Path("sibuwb_messages.json")
KEYWORDS = [
    "water disruption",
    "gangguan bekalan air",
    "scheduled interruption",
    "notis gangguan",
    "bekalan air",
    "water supply",
]


def load_config() -> tuple[int, str, str]:
    load_dotenv()
    api_id = int(os.getenv("TELEGRAM_API_ID", "0"))
    api_hash = os.getenv("TELEGRAM_API_HASH", "").strip()
    phone = os.getenv("TELEGRAM_PHONE", "").strip()

    if not api_id or not api_hash or not phone:
        raise ValueError(
            "Missing TELEGRAM_API_ID, TELEGRAM_API_HASH, or TELEGRAM_PHONE in .env"
        )
    return api_id, api_hash, phone


def normalize_message(message) -> dict:
    text = (message.message or "").strip()
    return {
        "id": message.id,
        "date": message.date.isoformat() if message.date else None,
        "text": text,
        "has_media": bool(message.media),
    }


def filter_messages(messages: list[dict], keywords: list[str]) -> list[dict]:
    keywords_lower = [k.lower() for k in keywords]
    matched = []
    for message in messages:
        text = (message.get("text") or "").lower()
        if any(keyword in text for keyword in keywords_lower):
            matched.append(message)
    return matched


async def ensure_login(client: TelegramClient, phone: str) -> None:
    if await client.is_user_authorized():
        return

    await client.send_code_request(phone)
    code = input("Enter Telegram login code: ").strip()
    try:
        await client.sign_in(phone, code)
    except SessionPasswordNeededError:
        password = input("Enter Telegram 2FA password: ").strip()
        await client.sign_in(password=password)


async def run_test() -> None:
    api_id, api_hash, phone = load_config()
    client = TelegramClient(SESSION_NAME, api_id, api_hash)

    await client.connect()
    try:
        await ensure_login(client, phone)
        target = (os.getenv("TELEGRAM_TEST_SOURCE") or CHANNEL_USERNAME).strip().lstrip("@")
        source_type = (os.getenv("TELEGRAM_TEST_SOURCE_TYPE") or "channel").strip().lower()

        if source_type == "bot":
            await client.send_message(target, "/start")
            print(f"Sent /start to @{target}.")
        else:
            try:
                await client(JoinChannelRequest(target))
                print(f"Joined channel @{target}.")
            except UserAlreadyParticipantError:
                print(f"Already in channel @{target}.")
            except Exception as exc:
                print(f"Join channel skipped for @{target}: {exc}")

        messages = []
        async for message in client.iter_messages(target, limit=DEFAULT_LIMIT):
            normalized = normalize_message(message)
            if normalized["text"]:
                messages.append(normalized)

        matched = filter_messages(messages, KEYWORDS)

        OUTPUT_FILE.write_text(
            json.dumps(
                {
                    "source": target,
                    "source_type": source_type,
                    "fetched_count": len(messages),
                    "matched_count": len(matched),
                    "keywords": KEYWORDS,
                    "messages": messages,
                    "matched_messages": matched,
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        print(f"Fetched messages: {len(messages)}")
        print(f"Keyword matches: {len(matched)}")
        print(f"Saved output to: {OUTPUT_FILE.resolve()}")

        if matched:
            print("\nTop matched messages:")
            for item in matched[:10]:
                print("-" * 60)
                print(f"ID: {item['id']}")
                print(f"Date: {item['date']}")
                print(item["text"])
    finally:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(run_test())
