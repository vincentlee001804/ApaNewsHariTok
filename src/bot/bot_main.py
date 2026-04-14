from __future__ import annotations

import asyncio
import html
import os
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from zoneinfo import ZoneInfo

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
    test_digest_push_command,
    test_push_command,
    dev_waze_command,
)
from src.core.config import (
    DB_CLEANUP_ENABLED,
    DB_CLEANUP_INTERVAL_HOURS,
    DB_RETENTION_DAYS,
    DIGEST_EVENING_HOUR_LOCAL,
    DIGEST_MORNING_HOUR_LOCAL,
    DIGEST_TRIGGER_WINDOW_MINUTES,
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
from src.ai.summarizer import generate_digest_greeting
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
    application.add_handler(CommandHandler("testdigestpush", test_digest_push_command))
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
            settings_callback, pattern="^settings_|^cat_|^freq_|^loc_|^area_kw_|^onb_"
        )
    )

    def _should_skip_push_message(text: str) -> bool:
        lowered = (text or "").lower()
        if not lowered.strip():
            return True
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

    def _is_digest_mode_preference(preference) -> bool:
        mode = (getattr(preference, "delivery_mode", "") or "").strip().lower()
        if mode in {"digest", "frequent"}:
            return mode == "digest"
        key = (getattr(preference, "frequency", "") or "").strip().lower()
        return key in {"digest_7am", "digest_8pm", "digest_7am_8pm"}

    def _digest_slots_for_preference(preference) -> list[int]:
        morning_hour = int(
            getattr(preference, "digest_morning_hour", None) or DIGEST_MORNING_HOUR_LOCAL
        )
        evening_hour = int(
            getattr(preference, "digest_evening_hour", None) or DIGEST_EVENING_HOUR_LOCAL
        )
        morning_enabled = bool(getattr(preference, "digest_morning_enabled", False))
        evening_enabled = bool(getattr(preference, "digest_evening_enabled", False))

        mode = (getattr(preference, "delivery_mode", "") or "").strip().lower()
        if mode not in {"digest", "frequent"}:
            legacy = (getattr(preference, "frequency", "") or "").strip().lower()
            if legacy == "digest_7am":
                morning_enabled, evening_enabled = True, False
            elif legacy == "digest_8pm":
                morning_enabled, evening_enabled = False, True
            elif legacy == "digest_7am_8pm":
                morning_enabled, evening_enabled = True, True

        # Product policy: digest is evening-only.
        morning_enabled = False
        if not evening_enabled:
            evening_enabled = True

        slots: list[int] = []
        if evening_enabled:
            slots.append(max(0, min(23, evening_hour)))
        return slots

    def _digest_slot_due_now(
        preference,
        *,
        now_utc: datetime,
        last_sent_utc: datetime | None,
    ) -> bool:
        slots = _digest_slots_for_preference(preference)
        if not slots:
            return False
        tz_name = (
            getattr(preference, "delivery_timezone", None)
            or SCHEDULED_PUSH_QUIET_TIMEZONE
        )
        try:
            tz = ZoneInfo(str(tz_name))
        except Exception:
            tz = ZoneInfo(SCHEDULED_PUSH_QUIET_TIMEZONE)

        now_local = now_utc.replace(tzinfo=timezone.utc).astimezone(tz)
        for slot_hour in slots:
            slot_dt_local = now_local.replace(
                hour=slot_hour, minute=0, second=0, microsecond=0
            )
            minutes_since_slot = (now_local - slot_dt_local).total_seconds() / 60.0
            if minutes_since_slot < 0 or minutes_since_slot >= DIGEST_TRIGGER_WINDOW_MINUTES:
                continue
            if last_sent_utc is None:
                return True
            last_local = last_sent_utc.replace(tzinfo=timezone.utc).astimezone(tz)
            if last_local < slot_dt_local:
                return True
        return False

    def _digest_period_name(preference, now_utc: datetime) -> str:
        tz_name = (
            getattr(preference, "delivery_timezone", None)
            or SCHEDULED_PUSH_QUIET_TIMEZONE
        )
        try:
            tz = ZoneInfo(str(tz_name))
        except Exception:
            tz = ZoneInfo(SCHEDULED_PUSH_QUIET_TIMEZONE)
        hour_local = now_utc.replace(tzinfo=timezone.utc).astimezone(tz).hour
        return "morning" if hour_local < 12 else "evening"

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
                    last_sent = preference.last_scheduled_push_at
                    if _is_digest_mode_preference(preference):
                        if not _digest_slot_due_now(
                            preference, now_utc=now, last_sent_utc=last_sent
                        ):
                            continue
                        from src.core.services import get_todays_news_digest_for_user

                        period_name = _digest_period_name(preference, now)
                        greeting = await asyncio.to_thread(
                            generate_digest_greeting, period_name
                        )
                        if not greeting:
                            greeting = (
                                "Good morning! Here is your Sarawak local news digest."
                                if period_name == "morning"
                                else "Good evening! Here is your Sarawak local news digest."
                            )
                        safe_greeting = html.escape(greeting)

                        text = await asyncio.to_thread(
                            get_todays_news_digest_for_user, telegram_id, 6
                        )
                    else:
                        safe_greeting = ""
                        interval_min = getattr(preference, "frequent_interval_minutes", None)
                        if interval_min is not None:
                            try:
                                interval_hours = max(1, int(interval_min)) / 60.0
                            except Exception:
                                interval_hours = _frequency_interval_hours(preference.frequency)
                        else:
                            interval_hours = _frequency_interval_hours(preference.frequency)
                        if last_sent and (now - last_sent) < timedelta(hours=interval_hours):
                            continue
                        text = await asyncio.to_thread(
                            lambda tid=telegram_id: get_latest_news_text_for_user(
                                tid, 1, scheduled_push=True
                            )
                        )
                    if _should_skip_push_message(text):
                        continue

                    if safe_greeting:
                        await application.bot.send_message(
                            chat_id=telegram_id,
                            text=safe_greeting,
                            parse_mode=ParseMode.HTML,
                        )
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



