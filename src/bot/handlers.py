from __future__ import annotations

from typing import Final

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes


WELCOME_TEXT: Final[str] = (
    "Hello! 👋\n\n"
    "I am the *Local News Summarization Bot*.\n"
    "Right now I can respond to a few basic commands:\n"
    "• /start – show this welcome message\n"
    "• /help – show available commands\n"
    "• /latest – (stub) show a placeholder latest news summary\n\n"
    "In the future, I will fetch and summarize real local news sources for you."
)


HELP_TEXT: Final[str] = (
    "*Available commands:*\n"
    "• /start – introduction and project description\n"
    "• /help – this help message\n"
    "• /latest – returns a demo summary (will be connected to real news later)\n"
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(WELCOME_TEXT, parse_mode=ParseMode.MARKDOWN)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(HELP_TEXT, parse_mode=ParseMode.MARKDOWN)


async def latest_demo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handler for /latest.
    Currently it fetches real headlines from configured RSS feeds
    and formats them as a simple list (no AI summarization yet).
    """
    # Local import to avoid circular dependency at import time
    from src.core.services import get_latest_news_text

    text = get_latest_news_text()
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

