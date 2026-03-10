from __future__ import annotations

import asyncio

from telegram.ext import ApplicationBuilder, CallbackQueryHandler, CommandHandler

from src.bot.handlers import (
    help_command,
    latest_demo,
    setareas_command,
    settings_callback,
    settings_command,
    start,
)
from src.core.config import PREFETCH_ENABLED, PREFETCH_INTERVAL_MINUTES, require_bot_token
from src.core.prefetch_service import prefetch_latest_articles_to_db
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
    application.add_handler(
        CallbackQueryHandler(
            settings_callback, pattern="^settings_|^cat_|^freq_|^loc_"
        )
    )

    async def _prefetch_job(context) -> None:
        # Run blocking RSS/network + DB writes in a thread to avoid blocking the bot event loop.
        try:
            inserted = await asyncio.to_thread(prefetch_latest_articles_to_db)
            if inserted:
                print(f"[prefetch] inserted {inserted} new articles")
        except Exception as e:
            print(f"[prefetch] error: {e}")

    if PREFETCH_ENABLED:
        # Prefetch immediately on startup, then repeat.
        application.job_queue.run_repeating(
            _prefetch_job,
            interval=PREFETCH_INTERVAL_MINUTES * 60,
            first=0,
            name="rss_prefetch",
        )
        print(
            f"RSS prefetch enabled: every {PREFETCH_INTERVAL_MINUTES} minutes (saving to database)"
        )

    print("Bot is starting... Press Ctrl+C to stop.")
    application.run_polling()


if __name__ == "__main__":
    main()



