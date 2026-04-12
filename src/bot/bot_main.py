from __future__ import annotations

import asyncio
import os
import threading
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram.constants import ParseMode

from telegram.ext import ApplicationBuilder, CallbackQueryHandler, CommandHandler, MessageHandler, filters

from src.bot.handlers import (
    help_command,
    conversational_message,
    ingest_channel_post,
    latest_demo,
    setareas_command,
    settings_callback,
    settings_command,
    start,
    test_push_command,
    dev_waze_command,
)
from src.core.config import (
    PREFETCH_ENABLED,
    PREFETCH_INTERVAL_MINUTES,
    TELEGRAM_SOURCE_CHANNELS,
    require_bot_token,
)
from src.core.config import DB_CLEANUP_ENABLED, DB_CLEANUP_INTERVAL_HOURS, DB_RETENTION_DAYS
from src.core.cleanup_service import cleanup_old_news_data
from src.core.prefetch_service import prefetch_latest_articles_to_db
from src.core.services import get_latest_news_text_for_user
from src.core.user_service import list_active_user_preferences
from src.storage.database import init_db


class _HealthHandler(BaseHTTPRequestHandler):
    """Minimal handler so Fly.io / other hosts can probe PORT (Telegram bot uses polling, not HTTP)."""

    def do_GET(self) -> None:
        self.send_response(200)
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        return


def _start_health_server_if_port_set() -> None:
    raw = (os.environ.get("PORT") or "").strip()
    if not raw:
        return
    try:
        port = int(raw)
    except ValueError:
        return
    if port <= 0:
        return
    server = HTTPServer(("0.0.0.0", port), _HealthHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()


def main() -> None:
    """
    Entry point for running the bot in polling mode.
    python-telegram-bot v21 manages its own asyncio event loop internally,
    so this function is synchronous and calls run_polling() directly.
    """
    _start_health_server_if_port_set()

    # Ensure database tables exist before the bot starts handling traffic.
    init_db()

    token = require_bot_token()

    application = ApplicationBuilder().token(token).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("latest", latest_demo))
    application.add_handler(CommandHandler("testpush", test_push_command))
    application.add_handler(CommandHandler("devwaze", dev_waze_command))
    application.add_handler(CommandHandler("setareas", setareas_command))
    application.add_handler(CommandHandler("settings", settings_command))
    application.add_handler(MessageHandler(filters.ChatType.CHANNEL, ingest_channel_post))
    application.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND,
            conversational_message,
        )
    )
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
                print(f"[prefetch] inserted {inserted} new row(s) into database (RSS + Telegram)")
            else:
                print(
                    "[prefetch] completed: 0 new rows (sources returned nothing new, "
                    "or all items already in DB / filtered)"
                )
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

    async def _cleanup_job(context) -> None:
        try:
            stats = await asyncio.to_thread(cleanup_old_news_data, DB_RETENTION_DAYS)
            if stats.get("articles_deleted", 0) or stats.get("deliveries_deleted", 0):
                print(
                    "[cleanup] deleted "
                    f"{stats.get('articles_deleted', 0)} articles and "
                    f"{stats.get('deliveries_deleted', 0)} delivery rows "
                    f"(retention={DB_RETENTION_DAYS} days)"
                )
        except Exception as e:
            print(f"[cleanup] error: {e}")

    if PREFETCH_ENABLED:
        # Prefetch immediately on startup, then repeat.
        application.job_queue.run_repeating(
            _prefetch_job,
            interval=PREFETCH_INTERVAL_MINUTES * 60,
            first=0,
            name="content_prefetch",
        )
        tg_note = (
            f" + Telegram ({len(TELEGRAM_SOURCE_CHANNELS)} source(s))"
            if TELEGRAM_SOURCE_CHANNELS
            else ""
        )
        print(
            f"Prefetch enabled: every {PREFETCH_INTERVAL_MINUTES} minutes "
            f"(RSS{tg_note} → database + frequency push)"
        )

    if DB_CLEANUP_ENABLED:
        application.job_queue.run_repeating(
            _cleanup_job,
            interval=DB_CLEANUP_INTERVAL_HOURS * 3600,
            first=60,
            name="db_cleanup",
        )
        print(
            "DB cleanup enabled: every "
            f"{DB_CLEANUP_INTERVAL_HOURS} hours (retention={DB_RETENTION_DAYS} days)"
        )

    print("Bot is starting... Press Ctrl+C to stop.")
    application.run_polling()


if __name__ == "__main__":
    main()



