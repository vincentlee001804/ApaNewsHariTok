from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, List

from telethon import TelegramClient
from telethon.errors import UserAlreadyParticipantError
from telethon.sessions import StringSession
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.types import Channel, User

from src.scrapers.rss_reader import RssItem


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _normalize_telegram_session_string(raw: str | None) -> str | None:
    """
    Fly.io / dashboard pastes sometimes inject newlines or spaces inside the base64 session
    string, which causes Telethon to raise binascii.Error: Incorrect padding.
    """
    if not raw or not str(raw).strip():
        return None
    # Remove all whitespace so a line-broken paste becomes one valid token.
    collapsed = "".join(str(raw).split())
    return collapsed or None


def normalize_telegram_post_url(url: str) -> str:
    """
    Canonical form for one Telegram post so the same message always maps to one DB `link`
    (dedup + unique constraint). Handles t.me vs telegram.me, http, trailing slashes,
    and username case.
    """
    if not url or not url.strip():
        return url
    u = url.strip().rstrip("/")
    low = u.lower()
    if low.startswith("http://"):
        u = "https://" + u[7:]
        low = u.lower()
    if low.startswith("https://telegram.me/"):
        u = "https://t.me/" + u[18:]
        low = u.lower()
    if not low.startswith("https://t.me/"):
        return url

    path = u[len("https://t.me/") :].split("?")[0].strip("/")
    parts = [p for p in path.split("/") if p]
    if len(parts) < 2:
        return u
    # Private / numeric channel: https://t.me/c/<internal_id>/<msg_id>
    if parts[0] == "c" and len(parts) >= 3 and parts[-1].isdigit():
        return f"https://t.me/c/{parts[1]}/{parts[-1]}"
    username, msg_id = parts[0], parts[-1]
    if not msg_id.isdigit():
        return u
    return f"https://t.me/{username.lower()}/{msg_id}"


def canonical_link_for_news_item(item: RssItem) -> str:
    """
    Single canonical `link` string for DB storage and dedup (RSS + Telegram + t.me mirrors).
    """
    raw = (item.link or "").strip()
    low_src = (item.source or "").lower()
    if low_src.startswith("telegram:"):
        return normalize_telegram_post_url(raw)
    low = raw.lower()
    if "t.me/" in low or "telegram.me/" in low:
        return normalize_telegram_post_url(raw)
    return raw


def _build_message_link(entity, message_id: int) -> str:
    username = getattr(entity, "username", None)
    if username:
        return normalize_telegram_post_url(
            f"https://t.me/{username.strip()}/{message_id}"
        )
    if isinstance(entity, Channel):
        return normalize_telegram_post_url(
            f"https://t.me/c/{entity.id}/{message_id}"
        )
    if isinstance(entity, User):
        uid = int(getattr(entity, "id", 0) or 0)
        return f"telegram://user/{uid}/{message_id}"
    chat_id = int(getattr(entity, "id", 0) or 0)
    return f"telegram://chat/{abs(chat_id)}/{message_id}"


def _message_to_item(message, entity, source_key: str) -> RssItem | None:
    text = (message.message or "").strip()
    if not text and not message.media:
        return None
    if not text and message.media:
        text = "(Media-only Telegram post)"

    first_line = text.splitlines()[0].strip() if text else "Telegram update"
    title = first_line[:140] if first_line else "Telegram update"
    published = message.date
    if published and published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)
    link = _build_message_link(entity, message.id)

    return RssItem(
        title=title,
        link=link,
        source=f"telegram:{source_key}",
        summary=text or None,
        published=published,
    )


async def _maybe_join_channel(client: TelegramClient, entity) -> None:
    """Subscribe to public broadcast channels so iter_messages can read history."""
    if not isinstance(entity, Channel):
        return
    if not getattr(entity, "broadcast", False):
        return
    try:
        await client(JoinChannelRequest(entity))
    except UserAlreadyParticipantError:
        pass
    except Exception:
        pass


async def _fetch_async(
    *,
    sources: Iterable[str],
    limit_per_source: int,
    max_age_hours: int,
    api_id: int,
    api_hash: str,
    session_name: str,
    session_string: str | None,
    auto_join_channels: bool,
) -> List[RssItem]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    items: List[RssItem] = []
    seen_links: set[str] = set()

    if session_string:
        client = TelegramClient(StringSession(session_string), api_id, api_hash)
    else:
        session_path = _project_root() / session_name
        client = TelegramClient(str(session_path), api_id, api_hash)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise RuntimeError(
                "Telegram source session is not authorized. "
                "Run test_sibuwb_bot.py locally to log in, copy TELEGRAM_SESSION_STRING for Fly, "
                "or use a .session file on disk."
            )

        for source in sources:
            try:
                entity = await client.get_entity(source)
            except Exception:
                continue

            if auto_join_channels:
                await _maybe_join_channel(client, entity)

            async for message in client.iter_messages(entity, limit=limit_per_source):
                if not message:
                    continue
                if message.date and message.date < cutoff:
                    continue

                item = _message_to_item(message, entity, source_key=source)
                if not item:
                    continue
                if item.link in seen_links:
                    continue
                seen_links.add(item.link)
                items.append(item)
    finally:
        await client.disconnect()

    return items


def fetch_latest_telegram_items(
    sources: Iterable[str],
    *,
    limit_per_source: int = 15,
    max_age_hours: int = 24,
) -> List[RssItem]:
    """
    Fetch recent text/media-caption messages from Telegram sources (channels/chats/bots)
    and map them into the project's RssItem format.
    """
    source_list = [s.strip() for s in sources if s.strip()]
    if not source_list:
        return []

    api_id_raw = (os.getenv("TELEGRAM_API_ID") or "").strip()
    api_hash = (os.getenv("TELEGRAM_API_HASH") or "").strip()
    _phone = (os.getenv("TELEGRAM_PHONE") or "").strip()
    session_string = _normalize_telegram_session_string(os.getenv("TELEGRAM_SESSION_STRING"))
    session_name = (os.getenv("TELEGRAM_SOURCE_SESSION_NAME") or "sibuwb_session").strip()
    auto_join = (os.getenv("TELEGRAM_AUTO_JOIN_CHANNELS", "true").strip().lower() in {
        "1", "true", "yes", "y", "on",
    })

    if not api_id_raw or not api_hash:
        return []
    # Phone was historically required; with TELEGRAM_SESSION_STRING (e.g. Fly.io) it is not.
    if not session_string and not _phone:
        return []

    try:
        api_id = int(api_id_raw)
    except ValueError:
        return []

    try:
        return asyncio.run(
            _fetch_async(
                sources=source_list,
                limit_per_source=limit_per_source,
                max_age_hours=max_age_hours,
                api_id=api_id,
                api_hash=api_hash,
                session_name=session_name,
                session_string=session_string,
                auto_join_channels=auto_join,
            )
        )
    except Exception as exc:
        err = str(exc).lower()
        hint = ""
        if "padding" in err:
            hint = (
                " (often: TELEGRAM_SESSION_STRING has newlines/spaces/truncation in Fly secrets — "
                "re-paste the full line from test_sibuwb_bot.py)"
            )
        print(f"[telegram] fetch failed: {exc}{hint}")
        return []
