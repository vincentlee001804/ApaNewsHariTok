from __future__ import annotations

import asyncio
import os
import threading
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram.constants import ParseMode
from telegram.error import Forbidden

from telegram.ext import ApplicationBuilder, CallbackQueryHandler, CommandHandler, MessageHandler, filters

from src.bot.handlers import (
    cancel_awaiting_area_keywords,
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
    DB_CLEANUP_ENABLED,
    DB_CLEANUP_INTERVAL_HOURS,
    DB_RETENTION_DAYS,
    PREFETCH_ENABLED,
    PREFETCH_INTERVAL_MINUTES,
    SCHEDULED_PUSH_GRACE_MINUTES_AFTER_FIRST_SEEN,
    SCHEDULED_PUSH_QUIET_END_HOUR_LOCAL,
    SCHEDULED_PUSH_QUIET_HOURS_ENABLED,
    SCHEDULED_PUSH_QUIET_START_HOUR_LOCAL,
    SCHEDULED_PUSH_QUIET_TIMEZONE,
    TELEGRAM_SOURCE_CHANNELS,
    is_scheduled_push_quiet_hours_now,
    print_ollama_config_banner,
    require_bot_token,
)
from src.core.cleanup_service import cleanup_old_news_data
from src.core.prefetch_service import prefetch_latest_articles_to_db
from src.core.services import (
    SCHEDULED_PUSH_SUMMARY_PENDING_SKIP_MARKER,
    get_latest_news_text_for_user,
)
from src.core.user_service import (
    list_active_user_preferences,
    set_user_active,
    touch_last_scheduled_push_at,
)
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
    application.add_handler(CommandHandler("cancel", cancel_awaiting_area_keywords))
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
            settings_callback, pattern="^settings_|^cat_|^freq_|^loc_|^area_kw_"
        )
    )

    def _should_skip_push_message(text: str) -> bool:
        lowered = (text or "").lower()
        skip_markers = [
            "no new headlines since your last request",
            "no news items match your current filters",
            "i couldn't fetch any news items right now",
            SCHEDULED_PUSH_SUMMARY_PENDING_SKIP_MARKER,
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

    async def _prefetch_db_job(context) -> None:
        """RSS + Telegram → database (and optional ai_summary backfill)."""
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

    async def _scheduled_push_job(context) -> None:
        """
        Per-user scheduled push: each row's preference.frequency (every_15m, every_30m, every_1h, …).
        Runs every 60s to evaluate who is due; not tied to the global fetch interval.
        """
        try:
            if is_scheduled_push_quiet_hours_now():
                return
            users = await asyncio.to_thread(list_active_user_preferences)
            now = datetime.utcnow()
            grace_after_start = timedelta(minutes=SCHEDULED_PUSH_GRACE_MINUTES_AFTER_FIRST_SEEN)
            for telegram_id, preference, first_seen_at in users:
                try:
                    if first_seen_at and (now - first_seen_at) < grace_after_start:
                        continue
                    interval_hours = _frequency_interval_hours(preference.frequency)
                    last_sent = preference.last_scheduled_push_at
                    if last_sent and (now - last_sent) < timedelta(hours=interval_hours):
                        continue

                    text = await asyncio.to_thread(
                        lambda tid=telegram_id: get_latest_news_text_for_user(
                            tid, 1, scheduled_push=True
                        )
                    )
                    if _should_skip_push_message(text):
                        continue

                    await application.bot.send_message(
                        chat_id=telegram_id,
                        text=text,
                        parse_mode=ParseMode.HTML,
                    )
                    await asyncio.to_thread(touch_last_scheduled_push_at, telegram_id, now)
                except Forbidden as e:
                    # User blocked the bot or chat is otherwise unavailable — stop scheduled pushes for them.
                    await asyncio.to_thread(set_user_active, telegram_id, False)
                    print(f"[push] deactivated user {telegram_id} (cannot deliver: {e})")
                except Exception as e:
                    print(f"[push] user {telegram_id} failed: {e}")
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
        application.job_queue.run_repeating(
            _prefetch_db_job,
            interval=PREFETCH_INTERVAL_MINUTES * 60,
            first=0,
            name="db_prefetch",
        )
        application.job_queue.run_repeating(
            _scheduled_push_job,
            interval=60,
            first=10,
            name="scheduled_push",
        )
        tg_note = (
            f" + Telegram ({len(TELEGRAM_SOURCE_CHANNELS)} source(s))"
            if TELEGRAM_SOURCE_CHANNELS
            else ""
        )
        print(
            f"Global fetch: every {PREFETCH_INTERVAL_MINUTES} min — RSS{tg_note} → database (same for all users)."
        )
        print(
            "Scheduled push: each user's /settings frequency (15m / 30m / 1h / …); eligibility checked every 60s."
        )
        if SCHEDULED_PUSH_QUIET_HOURS_ENABLED:
            print(
                "Scheduled push quiet hours: "
                f"{SCHEDULED_PUSH_QUIET_START_HOUR_LOCAL:02d}:00–{SCHEDULED_PUSH_QUIET_END_HOUR_LOCAL:02d}:00 "
                f"local ({SCHEDULED_PUSH_QUIET_TIMEZONE}); set SCHEDULED_PUSH_QUIET_HOURS_ENABLED=false to disable."
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

    print_ollama_config_banner()
    print("Bot is starting... Press Ctrl+C to stop.")
    application.run_polling()


if __name__ == "__main__":
    main()



