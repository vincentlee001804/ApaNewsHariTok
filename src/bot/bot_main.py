from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from telegram.constants import ParseMode

from telegram.ext import ApplicationBuilder, CallbackQueryHandler, CommandHandler, MessageHandler, filters

from src.bot.handlers import (
    help_command,
    ingest_channel_post,
    latest_demo,
    setareas_command,
    settings_callback,
    settings_command,
    start,
)
from src.core.config import PREFETCH_ENABLED, PREFETCH_INTERVAL_MINUTES, require_bot_token
from src.core.prefetch_service import prefetch_latest_articles_to_db
from src.core.services import get_latest_news_text_for_user
from src.core.user_service import list_active_user_preferences
from src.storage.database import init_db


def main() -> None:
    """
    Entry point for running the bot in polling mode.
    python-telegram-bot v21 manages its own asyncio event loop internally,
    so this function is synchronous and calls run_polling() directly.
    """
    # Ensure database tables exist before the bot starts handling traffic.
    init_db()

    token = require_bot_token()

    application = ApplicationBuilder().token(token).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("latest", latest_demo))
    application.add_handler(CommandHandler("setareas", setareas_command))
    application.add_handler(CommandHandler("settings", settings_command))
    application.add_handler(MessageHandler(filters.ChatType.CHANNEL, ingest_channel_post))
    application.add_handler(
        CallbackQueryHandler(
            settings_callback, pattern="^settings_|^cat_|^freq_|^loc_"
        )
    )

    last_push_tracker: dict[int, datetime] = {}

    def _should_skip_push_message(text: str) -> bool:
        lowered = (text or "").lower()
        skip_markers = [
            "no new headlines since your last request",
            "no news items match your current filters",
            "i couldn't fetch any news items right now",
        ]
        return any(marker in lowered for marker in skip_markers)

    def _frequency_interval_hours(value: str | None) -> float:
        mapping = {
            "every_15m": 0.25,
            "every_30m": 0.5,
            "every_1h": 1,
            "every_3h": 3,
            "every_6h": 6,
            "every_12h": 12,
            # Backward compatibility for existing rows:
            "instant": 1,
            "daily": 24,
        }
        key = (value or "").strip().lower()
        return mapping.get(key, 1)

    async def _prefetch_job(context) -> None:
        # Run blocking RSS/network + DB writes in a thread to avoid blocking the bot event loop.
        try:
            inserted = await asyncio.to_thread(prefetch_latest_articles_to_db)
            if inserted:
                print(f"[prefetch] inserted {inserted} new articles")
        except Exception as e:
            print(f"[prefetch] error: {e}")
            inserted = 0

        # Regular frequency push path (non-urgent).
        # Urgent alerts are handled immediately on ingest, not by scheduler.
        try:
            users = await asyncio.to_thread(list_active_user_preferences)
            now = datetime.utcnow()
            for telegram_id, preference in users:
                interval_hours = _frequency_interval_hours(preference.frequency)
                last_sent = last_push_tracker.get(telegram_id)
                if last_sent and (now - last_sent) < timedelta(hours=interval_hours):
                    continue

                # Scheduled push sends a single top-priority article per run.
                # /latest command remains multi-item (default handled in handlers/services).
                text = await asyncio.to_thread(
                    get_latest_news_text_for_user,
                    telegram_id,
                    1,
                )
                if _should_skip_push_message(text):
                    continue

                await application.bot.send_message(
                    chat_id=telegram_id,
                    text=text,
                    parse_mode=ParseMode.HTML,
                )
                last_push_tracker[telegram_id] = now
        except Exception as e:
            print(f"[push] error: {e}")

    if PREFETCH_ENABLED:
        # Prefetch immediately on startup, then repeat.
        application.job_queue.run_repeating(
            _prefetch_job,
            interval=PREFETCH_INTERVAL_MINUTES * 60,
            first=0,
            name="rss_prefetch",
        )
        print(
            f"RSS prefetch enabled: every {PREFETCH_INTERVAL_MINUTES} minutes (saving to database + frequency push)"
        )

    print("Bot is starting... Press Ctrl+C to stop.")
    application.run_polling()


if __name__ == "__main__":
    main()



