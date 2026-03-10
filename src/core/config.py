import os
from pathlib import Path
from typing import Final, List

from dotenv import load_dotenv


load_dotenv()


TELEGRAM_BOT_TOKEN: str | None = os.getenv("TELEGRAM_BOT_TOKEN")

DEFAULT_RSS_FEEDS: Final[List[str]] = [
    "https://www.sarawaktribune.com/feed/",
    "https://news.seehua.com/feed/",
    "https://www.theborneopost.com/feed/",
]


def _load_rss_feeds_from_file() -> List[str]:
    """
    Load RSS feed URLs from `RSS_Sources.txt` in the project root.

    File format:
    - One URL per line
    - Blank lines are ignored
    - Lines starting with '#' are ignored
    """
    # src/core/config.py -> project root is two levels up from `src/`
    project_root = Path(__file__).resolve().parents[2]
    sources_file = project_root / "RSS_Sources.txt"
    if not sources_file.exists():
        return []

    feeds: List[str] = []
    for raw_line in sources_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if not (line.startswith("http://") or line.startswith("https://")):
            continue
        feeds.append(line)

    # de-duplicate while preserving order
    return list(dict.fromkeys(feeds))


# RSS feeds are loaded from `RSS_Sources.txt` (project root) if present;
# otherwise we fall back to a small default list.
RSS_FEEDS: Final[List[str]] = _load_rss_feeds_from_file() or DEFAULT_RSS_FEEDS

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

# Background RSS prefetch (store to DB while bot is running)
PREFETCH_ENABLED: Final[bool] = os.getenv("PREFETCH_ENABLED", "true").strip().lower() in {
    "1",
    "true",
    "yes",
    "y",
    "on",
}

PREFETCH_INTERVAL_MINUTES: Final[int] = int(os.getenv("PREFETCH_INTERVAL_MINUTES", "10").strip() or "10")


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


