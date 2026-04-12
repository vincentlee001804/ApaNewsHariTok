from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Final

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatType, ParseMode
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes
from sqlalchemy.exc import IntegrityError
from sqlalchemy.exc import OperationalError

from src.core.config import (
    SCHEDULED_PUSH_GRACE_MINUTES_AFTER_FIRST_SEEN,
    TELEGRAM_SOURCE_CHANNELS,
    is_test_push_allowed,
)
from src.core.location_extractor import extract_location_and_state
from src.core.models import NewsArticle
from src.core.services import _extract_category, _is_urgent_utility_alert, build_urgent_preview
from src.core.user_service import (
    get_or_create_user,
    get_user_preference,
    is_user_active,
    list_active_user_preferences,
    set_user_active,
    update_user_preference,
)
from src.storage.database import SessionLocal

logger = logging.getLogger(__name__)

# user_data flag: after tapping Area Keywords, next plain-text message updates area_keywords.
AWAITING_AREA_KEYWORDS_UD_KEY: Final[str] = "awaiting_area_keywords"


def _normalize_area_keywords_raw(raw: str) -> str:
    """
    Comma-separated user input → stored lowercase comma-separated string.
    clear/none/off or empty → "".
    """
    value = (raw or "").strip()
    if value.lower() in {"clear", "none", "off"}:
        return ""
    keywords = [k.strip() for k in value.split(",") if k.strip()]
    return ",".join([k.lower() for k in keywords])


def _display_area_keywords_raw(raw: str) -> str:
    """Pretty display from the user's original comma-separated message."""
    return ", ".join([k.strip() for k in (raw or "").split(",") if k.strip()])


WELCOME_TEXT: Final[str] = (
    "Welcome! 👋\n\n"
    "I am an *AI Local News Summarization Bot*.\n"
    "I automatically gather local Sarawak news, use a local AI model to summarize "
    "lengthy articles into short ~30-word briefs, and send them directly to your Telegram "
    "as push notifications.\n\n"
    "*Quick start (30 seconds):*\n"
    "1) Tap /settings to choose categories/location/frequency\n"
    "2) Then use /latest to test\n\n"
    "*Main commands:*\n"
    "• /latest – latest personalized news with summaries\n"
    "• /settings – edit preferences and subscribe/unsubscribe scheduled pushes\n"
    "• /help – command guide"
)


HELP_TEXT: Final[str] = (
    "*Command guide:*\n"
    "• /start – welcome and quick start\n"
    "• /help – this help message\n"
    "• /latest – latest personalized news with summaries\n"
    "• /settings – categories, locations, area keywords, frequency, and subscribe/unsubscribe\n\n"
    "*Tips:*\n"
    "- Use Area Keywords for roads or neighborhoods (example: Jalan Song, Tabuan).\n"
    "- If you only want manual checks, set subscription to OFF inside /settings."
)

def _format_frequency(value: str | None) -> str:
    mapping = {
        "every_15m": "Every 15 mins",
        "every_30m": "Every 30 mins",
        "every_1h": "Every 1 hour",
        "every_3h": "Every 3 hours",
        "every_6h": "Every 6 hours",
        "every_12h": "Every 12 hours",
        # Backward compatibility for older stored values:
        "instant": "Every 1 hour",
        "daily": "Every 12 hours",
    }
    key = (value or "").strip().lower()
    return mapping.get(key, "Every 1 hour")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command. Register user if new."""
    if not update.message:
        return

    telegram_id = update.message.from_user.id
    username = update.message.from_user.username

    # Register or get user
    get_or_create_user(telegram_id, username)

    await update.message.reply_text(WELCOME_TEXT, parse_mode=ParseMode.MARKDOWN)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(HELP_TEXT, parse_mode=ParseMode.MARKDOWN)


async def latest_demo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handler for /latest.
    Fetches real headlines from configured RSS feeds with AI summaries,
    filtered by user preferences if set.
    """
    if not update.message:
        return

    telegram_id = update.message.from_user.id
    # Register user if new
    get_or_create_user(telegram_id, update.message.from_user.username)

    # Local import to avoid circular dependency at import time
    from src.core.services import get_latest_news_text_for_user

    try:
        text = await asyncio.to_thread(get_latest_news_text_for_user, telegram_id)
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)
    except OperationalError:
        logger.exception("Database busy while serving /latest")
        await update.message.reply_text(
            "The news database is busy right now. Please try /latest again in a few seconds."
        )


async def test_push_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Send one message that matches the scheduled job: get_latest_news_text_for_user(..., 1).
    Unlike the job, this always delivers the text (no skip when there is no news) so you can
    inspect formatting and empty states.
    """
    if not update.message or not update.effective_user:
        return

    if update.effective_chat.type != ChatType.PRIVATE:
        await update.message.reply_text("Use /testpush in a private chat with the bot.")
        return

    telegram_id = update.effective_user.id
    if not is_test_push_allowed(telegram_id):
        await update.message.reply_text(
            "Test push is turned off or your Telegram user ID is not on the allow list."
        )
        return

    get_or_create_user(telegram_id, update.effective_user.username)

    from src.core.services import get_latest_news_text_for_user

    try:
        text = await asyncio.to_thread(get_latest_news_text_for_user, telegram_id, 1)
    except OperationalError:
        logger.exception("Database busy while serving /testpush")
        await update.message.reply_text(
            "The news database is busy right now. Please try /testpush again in a few seconds."
        )
        return
    full = (
        "<b>[Test push]</b> Same build as the scheduled job (1 item, your filters):\n\n" + text
    )
    await update.message.reply_text(
        full,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


async def dev_waze_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Developer-only: show Waze block for the user's Area Keywords (403/errors allowed).
    Same access control as /testpush — not part of /latest or scheduled pushes.
    """
    if not update.message or not update.effective_user:
        return

    if update.effective_chat.type != ChatType.PRIVATE:
        await update.message.reply_text("Use /devwaze in a private chat with the bot.")
        return

    telegram_id = update.effective_user.id
    if not is_test_push_allowed(telegram_id):
        await update.message.reply_text(
            "Developer commands are disabled or your Telegram user ID is not on the allow list."
        )
        return

    get_or_create_user(telegram_id, update.effective_user.username)
    preference = get_user_preference(telegram_id)
    area = (preference.area_keywords or "").strip() if preference else ""

    from src.core.services import build_waze_section_for_area_keywords

    block = build_waze_section_for_area_keywords(area)
    if block is None:
        await update.message.reply_text(
            "Set Area Keywords first, e.g.:\n/setareas Jalan Example, Miri\n\n"
            "Waze preview filters alerts with those keywords."
        )
        return

    full = (
        "<b>[Dev — Waze only]</b>\n"
        "<i>Not included in /latest or scheduled pushes.</i>\n\n" + block
    )
    await update.message.reply_text(
        full,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


async def ingest_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Store approved Telegram channel posts as NewsArticle rows.
    The bot must be added to channels and allowed to receive channel posts.
    """
    message = update.channel_post
    if not message or not message.chat:
        return

    text = (message.text or message.caption or "").strip()
    if not text:
        return

    chat = message.chat
    username = (chat.username or "").strip().lower()
    chat_id_str = str(chat.id).strip().lower()

    # Safety: require allowlist; ignore channel posts if not configured.
    if not TELEGRAM_SOURCE_CHANNELS:
        return

    # Match either username (without @) or numeric chat id.
    if username not in TELEGRAM_SOURCE_CHANNELS and chat_id_str not in TELEGRAM_SOURCE_CHANNELS:
        return

    title_line = text.splitlines()[0].strip() if text.splitlines() else ""
    title = (title_line or f"Channel post {message.message_id}")[:500]
    source = f"Telegram: {chat.title or chat.username or chat.id}"

    if username:
        link = f"https://t.me/{username}/{message.message_id}"
    else:
        link = f"telegram://channel/{chat.id}/{message.message_id}"

    with SessionLocal() as session:
        location, state = extract_location_and_state(title, text)
        row = NewsArticle(
            title=title,
            link=link,
            source=source,
            raw_summary=text[:8000],
            location=location,
            state=state,
            category=_extract_category(title, text),
        )
        session.add(row)
        inserted = False
        try:
            session.commit()
            inserted = True
        except IntegrityError:
            session.rollback()

    # Urgent alerts bypass per-user frequency but share the post-signup grace with scheduled pushes.
    if inserted and _is_urgent_utility_alert(title, text):
        safe_title = title.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        preview = build_urgent_preview(title, text, max_words=45)
        safe_summary = preview.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        safe_source = source.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        lines = [
            "<b>🚨 Urgent Alert</b>",
            f"<blockquote><b>{safe_title}</b></blockquote>",
            f"<i>Summary</i>: {safe_summary}",
            f'<a href="{link}">{safe_source}</a>',
        ]
        message_text = "\n".join(lines)

        # Avoid blocking the asyncio event loop with synchronous DB access.
        users = await asyncio.to_thread(list_active_user_preferences)
        shared_sent = context.application.bot_data.setdefault("urgent_sent_links", {})
        now = datetime.utcnow()
        grace_after_start = timedelta(minutes=SCHEDULED_PUSH_GRACE_MINUTES_AFTER_FIRST_SEEN)

        semaphore = asyncio.Semaphore(5)

        async def _send_to_user(telegram_id: int, sent_links: set[str]) -> None:
            async with semaphore:
                if link in sent_links:
                    return
                try:
                    await context.bot.send_message(
                        chat_id=telegram_id,
                        text=message_text,
                        parse_mode=ParseMode.HTML,
                    )
                    sent_links.add(link)
                except Exception:
                    # Best-effort push per user; ignore failures.
                    pass

        send_tasks: list[asyncio.Task[None]] = []
        for telegram_id, preference, first_seen_at in users:
            if first_seen_at and (now - first_seen_at) < grace_after_start:
                continue
            if not preference.wants_urgent_alerts:
                continue
            sent_links = shared_sent.setdefault(telegram_id, set())
            if link in sent_links:
                continue
            send_tasks.append(
                asyncio.create_task(_send_to_user(telegram_id, sent_links))
            )

        if send_tasks:
            await asyncio.gather(*send_tasks)


async def setareas_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /setareas <comma separated keywords>
    Example:
      /setareas Jalan Wawasan, Taman Desa, Kampung Tabuan

    Stores area keywords to boost news priority when a headline or body mentions
    these roads/areas (other news still shown). Waze debugging uses the same
    keywords via /devwaze only — not sent in normal notifications.
    """
    if not update.message:
        return

    telegram_id = update.message.from_user.id
    get_or_create_user(telegram_id, update.message.from_user.username)

    raw = update.message.text or ""
    parts = raw.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await update.message.reply_text(
            "Usage:\n"
            "`/setareas Jalan Wawasan, Taman Desa`\n\n"
            "Tip: commas separate keywords. They *boost* matching news in rankings. "
            "For Waze, use `/devwaze` (developer only).\n"
            "Send `/setareas` with an empty value to clear.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    value = parts[1].strip()
    normalized = _normalize_area_keywords_raw(value)

    update_user_preference(telegram_id, area_keywords=normalized)

    if normalized:
        display = _display_area_keywords_raw(value)
        await update.message.reply_text(
            f"✅ Area keywords updated:\n{display}\n\n"
            "News mentioning these areas ranks higher. Waze is not included in "
            "normal messages — use `/devwaze` to preview map alerts (developer).",
            parse_mode=ParseMode.MARKDOWN,
        )
    else:
        await update.message.reply_text(
            "✅ Area keywords cleared. News ranking no longer uses them.",
            parse_mode=ParseMode.MARKDOWN,
        )


async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /settings command. Show current preferences and options."""
    if not update.message:
        return

    context.user_data.pop(AWAITING_AREA_KEYWORDS_UD_KEY, None)

    telegram_id = update.message.from_user.id
    get_or_create_user(telegram_id, update.message.from_user.username)

    preference = get_user_preference(telegram_id)

    if not preference:
        await update.message.reply_text(
            "Error: Could not load your preferences. Please try again.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    # Format current settings
    categories_display = preference.categories if preference.categories else "All categories"
    
    # Format locations nicely
    if preference.locations:
        location_list = [loc.strip().title() for loc in preference.locations.split(",") if loc.strip()]
        locations_display = ", ".join(location_list) if location_list else "All Sarawak"
    else:
        locations_display = "All Sarawak"
    
    frequency_display = _format_frequency(preference.frequency)
    urgent_display = "Yes" if preference.wants_urgent_alerts else "No"
    subscription_display = "ON" if is_user_active(telegram_id) else "OFF"
    area_keywords_display = (
        ", ".join([k.strip().title() for k in (preference.area_keywords or "").split(",") if k.strip()])
        if (preference.area_keywords or "").strip()
        else "None"
    )

    settings_text = (
        "*Your Current Settings:*\n\n"
        f"📂 *Categories:* {categories_display}\n"
        f"📍 *Locations:* {locations_display}\n"
        f"🗺️ *Area Keywords:* {area_keywords_display}\n"
        f"⏰ *Frequency:* {frequency_display}\n"
        f"🔔 *Subscription:* {subscription_display}\n"
        f"🚨 *Urgent Alerts:* {urgent_display}\n\n"
        "Use the buttons below to change your preferences:"
    )

    # Create inline keyboard
    keyboard = [
        [
            InlineKeyboardButton("📂 Categories", callback_data="settings_categories"),
            InlineKeyboardButton("📍 Locations", callback_data="settings_locations"),
        ],
        [
            InlineKeyboardButton("🗺️ Area Keywords", callback_data="settings_area_keywords"),
        ],
        [
            InlineKeyboardButton("⏰ Frequency", callback_data="settings_frequency"),
        ],
        [
            InlineKeyboardButton(
                "🚨 Urgent Alerts: " + ("ON" if preference.wants_urgent_alerts else "OFF"),
                callback_data="settings_toggle_urgent",
            ),
        ],
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        settings_text, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup
    )


async def settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle callback queries from settings inline buttons."""
    query = update.callback_query
    if not query or not query.data:
        return

    await query.answer()

    telegram_id = query.from_user.id
    preference = get_user_preference(telegram_id)

    if not preference:
        await query.edit_message_text("Error: Could not load your preferences.")
        return

    data = query.data

    if data != "settings_area_keywords":
        context.user_data.pop(AWAITING_AREA_KEYWORDS_UD_KEY, None)

    if data == "settings_categories":
        # Show category selection
        keyboard = [
            [
                InlineKeyboardButton("All Categories", callback_data="cat_all"),
                InlineKeyboardButton("Sarawak Only", callback_data="cat_sarawak"),
            ],
            [
                InlineKeyboardButton("Sports", callback_data="cat_sports"),
                InlineKeyboardButton("Politics", callback_data="cat_politics"),
            ],
            [
                InlineKeyboardButton("Custom (comma-separated)", callback_data="cat_custom"),
            ],
            [InlineKeyboardButton("◀️ Back", callback_data="settings_back")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            "*Select Categories:*\n\n"
            "Choose which news categories you want to see. "
            "You can select multiple by choosing 'Custom'.\n\n"
            "Current: " + (preference.categories if preference.categories else "All"),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=reply_markup,
        )

    elif data == "settings_area_keywords":
        context.user_data[AWAITING_AREA_KEYWORDS_UD_KEY] = True
        current = (
            ", ".join([k.strip().title() for k in (preference.area_keywords or "").split(",") if k.strip()])
            if (preference.area_keywords or "").strip()
            else "None"
        )
        keyboard = [
            [InlineKeyboardButton("🗑️ Clear all keywords", callback_data="area_kw_clear")],
            [InlineKeyboardButton("◀️ Back", callback_data="settings_back")],
        ]
        await query.edit_message_text(
            "*Area Keywords*\n\n"
            "Type your roads or areas in *one message*, separated by commas, then send.\n"
            "Example:\n"
            "`Jalan Wawasan, Taman Desa, Kampung Tabuan`\n\n"
            "• Matching news gets *higher priority*; other news is still shown.\n"
            "• Waze is not included in `/latest` or pushes (`/devwaze` is developer-only).\n"
            "• Send `clear`, `none`, or `off` as the message to remove all keywords.\n"
            "• `/setareas …` still works if you prefer a command.\n"
            "• `/cancel` exits this step without saving.\n\n"
            f"*Current:* {current}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return

    elif data == "area_kw_clear":
        update_user_preference(telegram_id, area_keywords="")
        await settings_callback_refresh(query, telegram_id)
        return

    elif data == "settings_locations":

        # Show location selection with checkmarks
        current_locations = [loc.strip().lower() for loc in (preference.locations or "").split(",") if loc.strip()]
        
        def is_selected(loc_key: str) -> str:
            location_map = {
                "loc_kuching": "kuching",
                "loc_kota_samarahan": "kota samarahan",
                "loc_miri": "miri",
                "loc_sibu": "sibu",
                "loc_bintulu": "bintulu",
                "loc_serian": "serian",
                "loc_sarikei": "sarikei",
            }
            return " ✓" if location_map.get(loc_key, "").lower() in current_locations else ""
        
        keyboard = [
            [
                InlineKeyboardButton(
                    "All Sarawak" + (" ✓" if not current_locations else ""),
                    callback_data="loc_all",
                ),
            ],
            [
                InlineKeyboardButton("Kuching" + is_selected("loc_kuching"), callback_data="loc_kuching"),
                InlineKeyboardButton("Kota Samarahan" + is_selected("loc_kota_samarahan"), callback_data="loc_kota_samarahan"),
            ],
            [
                InlineKeyboardButton("Miri" + is_selected("loc_miri"), callback_data="loc_miri"),
                InlineKeyboardButton("Sibu" + is_selected("loc_sibu"), callback_data="loc_sibu"),
            ],
            [
                InlineKeyboardButton("Bintulu" + is_selected("loc_bintulu"), callback_data="loc_bintulu"),
                InlineKeyboardButton("Serian" + is_selected("loc_serian"), callback_data="loc_serian"),
            ],
            [
                InlineKeyboardButton("Sarikei" + is_selected("loc_sarikei"), callback_data="loc_sarikei"),
                InlineKeyboardButton("More Cities...", callback_data="loc_more"),
            ],
            [InlineKeyboardButton("◀️ Back", callback_data="settings_back")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        current_display = ", ".join([loc.title() for loc in current_locations]) if current_locations else "All Sarawak"
        await query.edit_message_text(
            "*Select Locations:*\n\n"
            "Choose which Sarawak cities you want news from. "
            "You can select multiple cities.\n\n"
            f"Current: {current_display}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=reply_markup,
        )

    elif data == "loc_more":
        # Show more cities with checkmarks
        current_locations = [loc.strip().lower() for loc in (preference.locations or "").split(",") if loc.strip()]
        
        def is_selected_more(loc_key: str) -> str:
            location_map = {
                "loc_sri_aman": "sri aman",
                "loc_mukah": "mukah",
                "loc_limbang": "limbang",
                "loc_lawas": "lawas",
                "loc_betong": "betong",
                "loc_saratok": "saratok",
                "loc_kapit": "kapit",
                "loc_marudi": "marudi",
                "loc_belaga": "belaga",
            }
            return " ✓" if location_map.get(loc_key, "").lower() in current_locations else ""
        
        keyboard = [
            [
                InlineKeyboardButton("Sri Aman" + is_selected_more("loc_sri_aman"), callback_data="loc_sri_aman"),
                InlineKeyboardButton("Mukah" + is_selected_more("loc_mukah"), callback_data="loc_mukah"),
            ],
            [
                InlineKeyboardButton("Limbang" + is_selected_more("loc_limbang"), callback_data="loc_limbang"),
                InlineKeyboardButton("Lawas" + is_selected_more("loc_lawas"), callback_data="loc_lawas"),
            ],
            [
                InlineKeyboardButton("Betong" + is_selected_more("loc_betong"), callback_data="loc_betong"),
                InlineKeyboardButton("Saratok" + is_selected_more("loc_saratok"), callback_data="loc_saratok"),
            ],
            [
                InlineKeyboardButton("Kapit" + is_selected_more("loc_kapit"), callback_data="loc_kapit"),
                InlineKeyboardButton("Marudi" + is_selected_more("loc_marudi"), callback_data="loc_marudi"),
            ],
            [
                InlineKeyboardButton("Belaga" + is_selected_more("loc_belaga"), callback_data="loc_belaga"),
            ],
            [InlineKeyboardButton("◀️ Back to Locations", callback_data="settings_locations")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        current_display = ", ".join([loc.title() for loc in current_locations]) if current_locations else "All Sarawak"
        await query.edit_message_text(
            "*More Sarawak Cities:*\n\n"
            "Select additional cities for news filtering.\n\n"
            f"Current: {current_display}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=reply_markup,
        )

    elif data.startswith("loc_"):
        # Location selection
        location_map = {
            "loc_all": "",
            "loc_kuching": "kuching",
            "loc_kota_samarahan": "kota samarahan",
            "loc_miri": "miri",
            "loc_sibu": "sibu",
            "loc_bintulu": "bintulu",
            "loc_serian": "serian",
            "loc_sarikei": "sarikei",
            "loc_sri_aman": "sri aman",
            "loc_mukah": "mukah",
            "loc_limbang": "limbang",
            "loc_lawas": "lawas",
            "loc_betong": "betong",
            "loc_saratok": "saratok",
            "loc_kapit": "kapit",
            "loc_marudi": "marudi",
            "loc_belaga": "belaga",
        }

        if data == "loc_all":
            update_user_preference(telegram_id, locations="")
            await query.answer("Locations set to: All Sarawak")
        else:
            selected_location = location_map.get(data, "")
            if selected_location:
                # Get current locations and add/remove the selected one
                current_locations = preference.locations or ""
                location_list = [loc.strip().lower() for loc in current_locations.split(",") if loc.strip()]

                if selected_location.lower() in location_list:
                    # Remove if already selected
                    location_list.remove(selected_location.lower())
                    await query.answer(f"{selected_location.title()} removed")
                else:
                    # Add if not selected
                    location_list.append(selected_location.lower())
                    await query.answer(f"{selected_location.title()} added")

                new_locations = ",".join(sorted(location_list))
                update_user_preference(telegram_id, locations=new_locations)

        await settings_callback_refresh(query, telegram_id)

    elif data == "settings_frequency":
        # Show frequency selection
        current_freq = (preference.frequency or "").strip().lower()
        if current_freq == "instant":
            current_freq = "every_1h"
        elif current_freq == "daily":
            current_freq = "every_12h"

        keyboard = [
            [
                InlineKeyboardButton(
                    "⏱️ Every 15 mins" + (" ✓" if current_freq == "every_15m" else ""),
                    callback_data="freq_every_15m",
                ),
            ],
            [
                InlineKeyboardButton(
                    "⏱️ Every 30 mins" + (" ✓" if current_freq == "every_30m" else ""),
                    callback_data="freq_every_30m",
                ),
            ],
            [
                InlineKeyboardButton(
                    "⏱️ Every 1 hour" + (" ✓" if current_freq == "every_1h" else ""),
                    callback_data="freq_every_1h",
                ),
            ],
            [
                InlineKeyboardButton(
                    "⏱️ Every 3 hours" + (" ✓" if current_freq == "every_3h" else ""),
                    callback_data="freq_every_3h",
                ),
            ],
            [
                InlineKeyboardButton(
                    "⏱️ Every 6 hours" + (" ✓" if current_freq == "every_6h" else ""),
                    callback_data="freq_every_6h",
                ),
            ],
            [
                InlineKeyboardButton(
                    "⏱️ Every 12 hours" + (" ✓" if current_freq == "every_12h" else ""),
                    callback_data="freq_every_12h",
                ),
            ],
            [
                InlineKeyboardButton("✅ Subscribe scheduled push", callback_data="freq_subscribe"),
                InlineKeyboardButton("⏸️ Unsubscribe", callback_data="freq_unsubscribe"),
            ],
            [InlineKeyboardButton("◀️ Back", callback_data="settings_back")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            "*Select Frequency:*\n\n"
            "Choose how often you want push notifications while the bot is running.\n\n"
            "Use Subscribe/Unsubscribe below to turn scheduled pushes ON/OFF.\n\n"
            f"Current: {_format_frequency(preference.frequency)}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=reply_markup,
        )

    elif data == "settings_toggle_urgent":
        # Toggle urgent alerts
        new_value = not preference.wants_urgent_alerts
        update_user_preference(telegram_id, wants_urgent_alerts=new_value)
        await query.answer(f"Urgent alerts {'enabled' if new_value else 'disabled'}!")
        # Refresh settings view
        await settings_callback_refresh(query, telegram_id)

    elif data.startswith("cat_"):
        # Category selection
        if data == "cat_all":
            update_user_preference(telegram_id, categories="")
            await query.answer("Categories set to: All")
        elif data == "cat_sarawak":
            update_user_preference(telegram_id, categories="sarawak")
            await query.answer("Categories set to: Sarawak only")
        elif data == "cat_sports":
            update_user_preference(telegram_id, categories="sports")
            await query.answer("Categories set to: Sports")
        elif data == "cat_politics":
            update_user_preference(telegram_id, categories="politics")
            await query.answer("Categories set to: Politics")
        elif data == "cat_custom":
            await query.edit_message_text(
                "To set custom categories, send me a message like:\n"
                "`/setcategories sarawak,sports,politics`\n\n"
                "Or use single words separated by commas.",
                parse_mode=ParseMode.MARKDOWN,
            )
            return
        await settings_callback_refresh(query, telegram_id)

    elif data.startswith("freq_"):
        # Frequency selection
        if data == "freq_every_15m":
            update_user_preference(telegram_id, frequency="every_15m")
            await query.answer("Frequency set to: Every 15 mins")
        elif data == "freq_every_30m":
            update_user_preference(telegram_id, frequency="every_30m")
            await query.answer("Frequency set to: Every 30 mins")
        elif data == "freq_every_1h":
            update_user_preference(telegram_id, frequency="every_1h")
            await query.answer("Frequency set to: Every 1 hour")
        elif data == "freq_every_3h":
            update_user_preference(telegram_id, frequency="every_3h")
            await query.answer("Frequency set to: Every 3 hours")
        elif data == "freq_every_6h":
            update_user_preference(telegram_id, frequency="every_6h")
            await query.answer("Frequency set to: Every 6 hours")
        elif data == "freq_every_12h":
            update_user_preference(telegram_id, frequency="every_12h")
            await query.answer("Frequency set to: Every 12 hours")
        elif data == "freq_subscribe":
            set_user_active(telegram_id, True)
            await query.answer("Subscribed: scheduled pushes ON")
        elif data == "freq_unsubscribe":
            set_user_active(telegram_id, False)
            await query.answer("Unsubscribed: scheduled pushes OFF")
        await settings_callback_refresh(query, telegram_id)

    elif data == "settings_back":
        # Return to main settings
        await settings_callback_refresh(query, telegram_id)


async def settings_callback_refresh(query, telegram_id: int) -> None:
    """Helper to refresh the settings view after a change."""
    preference = get_user_preference(telegram_id)
    if not preference:
        return

    categories_display = preference.categories if preference.categories else "All categories"
    
    # Format locations nicely
    if preference.locations:
        location_list = [loc.strip().title() for loc in preference.locations.split(",") if loc.strip()]
        locations_display = ", ".join(location_list) if location_list else "All Sarawak"
    else:
        locations_display = "All Sarawak"
    
    frequency_display = _format_frequency(preference.frequency)
    urgent_display = "Yes" if preference.wants_urgent_alerts else "No"
    subscription_display = "ON" if is_user_active(telegram_id) else "OFF"
    area_keywords_display = (
        ", ".join([k.strip().title() for k in (preference.area_keywords or "").split(",") if k.strip()])
        if (preference.area_keywords or "").strip()
        else "None"
    )

    settings_text = (
        "*Your Current Settings:*\n\n"
        f"📂 *Categories:* {categories_display}\n"
        f"📍 *Locations:* {locations_display}\n"
        f"🗺️ *Area Keywords:* {area_keywords_display}\n"
        f"⏰ *Frequency:* {frequency_display}\n"
        f"🔔 *Subscription:* {subscription_display}\n"
        f"🚨 *Urgent Alerts:* {urgent_display}\n\n"
        "Use the buttons below to change your preferences:"
    )

    keyboard = [
        [
            InlineKeyboardButton("📂 Categories", callback_data="settings_categories"),
            InlineKeyboardButton("📍 Locations", callback_data="settings_locations"),
        ],
        [
            InlineKeyboardButton("🗺️ Area Keywords", callback_data="settings_area_keywords"),
        ],
        [
            InlineKeyboardButton("⏰ Frequency", callback_data="settings_frequency"),
        ],
        [
            InlineKeyboardButton(
                "🚨 Urgent Alerts: " + ("ON" if preference.wants_urgent_alerts else "OFF"),
                callback_data="settings_toggle_urgent",
            ),
        ],
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(
        settings_text, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup
    )


def _looks_like_todays_summary_request(message_text: str) -> bool:
    lowered = (message_text or "").strip().lower()
    if not lowered:
        return False

    has_summary = any(
        kw in lowered for kw in ["summary", "summarise", "summarize", "ringkasan", "ringkasan berita"]
    )
    has_today = any(kw in lowered for kw in ["today", "todays", "hari ini", "tadi"])
    has_news = any(kw in lowered for kw in ["news", "berita", "headlines"])

    # Require summary + today, or summary + news + "today-ish".
    return (has_summary and has_today) or (has_summary and has_news and has_today)


async def conversational_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Option B: conversational mode.
    User can type a question like: "summary for today's news",
    and the bot replies with one digest generated from local DB + Ollama.
    """
    if not update.message or not update.message.text:
        return

    telegram_id = update.message.from_user.id
    username = update.message.from_user.username
    get_or_create_user(telegram_id, username)

    message_text = update.message.text
    lowered = (message_text or "").strip().lower()

    if context.user_data.get(AWAITING_AREA_KEYWORDS_UD_KEY):
        stripped = (message_text or "").strip()
        if not stripped:
            return
        if lowered == "cancel":
            context.user_data.pop(AWAITING_AREA_KEYWORDS_UD_KEY, None)
            await update.message.reply_text(
                "Cancelled. Open /settings when you want to set area keywords.",
                parse_mode=ParseMode.MARKDOWN,
            )
            return
        normalized = _normalize_area_keywords_raw(stripped)
        update_user_preference(telegram_id, area_keywords=normalized)
        context.user_data.pop(AWAITING_AREA_KEYWORDS_UD_KEY, None)
        if normalized:
            display = _display_area_keywords_raw(stripped)
            await update.message.reply_text(
                f"✅ Area keywords saved:\n{display}\n\n"
                "News mentioning these areas ranks higher. Use /settings to change again.",
                parse_mode=ParseMode.MARKDOWN,
            )
        else:
            await update.message.reply_text(
                "✅ Area keywords cleared. News ranking no longer uses them.",
                parse_mode=ParseMode.MARKDOWN,
            )
        return

    # Friendly shortcuts.
    if any(greet in lowered for greet in ["hi", "hello", "hey", "good morning", "good night"]):
        await update.message.reply_text(
            "Ask me for:\n"
            "- `today summary` (or `ringkasan berita hari ini`)\n"
            "- `latest`\n"
            "- or ask a question like: 'What happened with water supply today?'\n",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    if "latest" in lowered:
        from src.core.services import get_latest_news_text_for_user

        text = await asyncio.to_thread(get_latest_news_text_for_user, telegram_id, 3)
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)
        return

    if _looks_like_todays_summary_request(message_text):
        from src.core.services import get_todays_news_digest_for_user

        digest = await asyncio.to_thread(get_todays_news_digest_for_user, telegram_id, 6)
        if not digest:
            await update.message.reply_text(
                "I couldn't generate today's summary yet. Try `/latest`.",
                parse_mode=ParseMode.HTML,
            )
            return

        await update.message.reply_text(
            digest,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
        return

    # Default: "news agent" Q&A mode.
    from src.core.services import get_news_agent_response_for_user

    response = await asyncio.to_thread(
        get_news_agent_response_for_user, telegram_id, message_text
    )
    await update.message.reply_text(response, parse_mode=ParseMode.HTML)


async def cancel_awaiting_area_keywords(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Exit Area Keywords text-input step (see AWAITING_AREA_KEYWORDS_UD_KEY)."""
    if not update.message:
        return
    if context.user_data.pop(AWAITING_AREA_KEYWORDS_UD_KEY, None):
        await update.message.reply_text(
            "Cancelled area keywords. Open /settings when you want to try again.",
            parse_mode=ParseMode.MARKDOWN,
        )
