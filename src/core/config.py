import os
from typing import Final, List

from dotenv import load_dotenv


load_dotenv()


TELEGRAM_BOT_TOKEN: str | None = os.getenv("TELEGRAM_BOT_TOKEN")

# Default RSS feeds for Sarawak news. You can modify this list as needed.
RSS_FEEDS: Final[List[str]] = [
    "https://www.sarawaktribune.com/feed/",
    "https://news.seehua.com/feed/",
    "https://www.theborneopost.com/feed/",
]

# Deduplication: when enabled, once an article has been sent, it will never be sent again.
# For testing message formats, you may want to disable this temporarily.
# Set in `.env` as: DEDUPLICATION_ENABLED=false
DEDUPLICATION_ENABLED: Final[bool] = os.getenv("DEDUPLICATION_ENABLED", "true").strip().lower() in {
    "1",
    "true",
    "yes",
    "y",
    "on",
}


def require_bot_token() -> str:
    """
    Retrieve the Telegram bot token or raise a clear error message.
    """
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is not set. "
            "Create a .env file or set the environment variable before running the bot."
        )
    return TELEGRAM_BOT_TOKEN


