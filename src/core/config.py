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

# Optional Telegram channel sources for ingesting channel posts into DB.
# Comma-separated values in .env, each can be:
# - channel username without @, e.g. swbnews
# - numeric chat id, e.g. -1001234567890
TELEGRAM_SOURCE_CHANNELS: Final[List[str]] = [
    x.strip().lower()
    for x in os.getenv("TELEGRAM_SOURCE_CHANNELS", "").split(",")
    if x.strip()
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

# Background RSS prefetch (store to DB while bot is running)
PREFETCH_ENABLED: Final[bool] = os.getenv("PREFETCH_ENABLED", "true").strip().lower() in {
    "1",
    "true",
    "yes",
    "y",
    "on",
}

PREFETCH_INTERVAL_MINUTES: Final[int] = int(os.getenv("PREFETCH_INTERVAL_MINUTES", "10").strip() or "10")


def _env_float(key: str, default: str) -> float:
    return float((os.getenv(key, default) or default).strip() or default)


# Waze Live Map (unofficial georss JSON used by the browser map). Bounding box = Sarawak by default.
WAZE_GEO_RSS_URL: Final[str] = (
    (os.getenv("WAZE_GEO_RSS_URL") or "https://www.waze.com/live-map/api/georss").strip()
)
WAZE_BBOX_TOP: Final[float] = _env_float("WAZE_BBOX_TOP", "5.0")
WAZE_BBOX_BOTTOM: Final[float] = _env_float("WAZE_BBOX_BOTTOM", "0.8")
WAZE_BBOX_LEFT: Final[float] = _env_float("WAZE_BBOX_LEFT", "109.5")
WAZE_BBOX_RIGHT: Final[float] = _env_float("WAZE_BBOX_RIGHT", "115.8")
WAZE_ENV: Final[str] = (os.getenv("WAZE_ENV", "row").strip() or "row").lower()
# Comma-separated alert `type` values to include (Waze strings, e.g. ACCIDENT, JAM).
_DEFAULT_WAZE_ALERT_TYPES: Final[str] = (
    "ACCIDENT,JAM,HAZARD,ROAD_CLOSED,CONSTRUCTION,WEATHERHAZARD"
)
WAZE_ALERT_TYPES: Final[List[str]] = [
    x.strip().upper()
    for x in (os.getenv("WAZE_ALERT_TYPES", _DEFAULT_WAZE_ALERT_TYPES) or _DEFAULT_WAZE_ALERT_TYPES).split(
        ","
    )
    if x.strip()
]
WAZE_INCLUDE_POLICE: Final[bool] = os.getenv("WAZE_INCLUDE_POLICE", "false").strip().lower() in {
    "1",
    "true",
    "yes",
    "y",
    "on",
}
# If Waze returns 403, paste a browser Cookie header from DevTools (same session as live map).
WAZE_COOKIE: Final[str | None] = (os.getenv("WAZE_COOKIE") or "").strip() or None
WAZE_REQUEST_TIMEOUT_SEC: Final[int] = int(
    (os.getenv("WAZE_REQUEST_TIMEOUT_SEC") or "25").strip() or "25"
)


def waze_allowed_alert_types() -> List[str]:
    """
    Resolved list of Waze alert type strings to keep (uppercase).
    An empty configured list means no filter (all types returned by Waze are kept).
    """
    types = list(dict.fromkeys(WAZE_ALERT_TYPES))  # de-dupe, preserve order
    if WAZE_INCLUDE_POLICE and "POLICE" not in types:
        types.append("POLICE")
    return types


def waze_allowed_type_set() -> set[str]:
    """
    Set used for filtering. Empty set = accept all alert types.
    """
    types = waze_allowed_alert_types()
    return set() if not types else {t.upper() for t in types}


# /testpush and /devwaze: developer commands (scheduled preview + Waze-only preview). Disable in production if desired.
TEST_PUSH_ENABLED: Final[bool] = os.getenv("TEST_PUSH_ENABLED", "true").strip().lower() in {
    "1",
    "true",
    "yes",
    "y",
    "on",
}


def _parse_telegram_id_list(raw: str) -> List[int]:
    ids: List[int] = []
    for part in (raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.append(int(part))
        except ValueError:
            continue
    return ids


# If non-empty, only these numeric user IDs may use /testpush and /devwaze. If empty, any private-chat user may use them.
TEST_PUSH_ALLOWED_TELEGRAM_IDS: Final[List[int]] = _parse_telegram_id_list(
    os.getenv("TEST_PUSH_ALLOWED_TELEGRAM_IDS", "")
)


def is_test_push_allowed(telegram_id: int) -> bool:
    if not TEST_PUSH_ENABLED:
        return False
    if not TEST_PUSH_ALLOWED_TELEGRAM_IDS:
        return True
    return telegram_id in TEST_PUSH_ALLOWED_TELEGRAM_IDS


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


