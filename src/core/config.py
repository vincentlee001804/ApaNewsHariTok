import os
from datetime import datetime
from pathlib import Path
from typing import Final, List, Tuple
from urllib.parse import urlparse

from dotenv import load_dotenv


load_dotenv()


TELEGRAM_BOT_TOKEN: str | None = os.getenv("TELEGRAM_BOT_TOKEN")

# Ollama: primary = local or any URL. Optional OLLAMA_API_BASE_FALLBACK = Ollama Cloud when primary is down.
# Cloud: https://ollama.com + API key (https://ollama.com/settings/keys). Use a cloud tag, e.g. ministral-3:8b-cloud.
_OLLAMA_API_BASE: Final[str] = (
    (os.getenv("OLLAMA_API_BASE", "http://localhost:11434").strip() or "http://localhost:11434").rstrip("/")
)
OLLAMA_GENERATE_URL: Final[str] = f"{_OLLAMA_API_BASE}/api/generate"
OLLAMA_API_KEY: Final[str | None] = (os.getenv("OLLAMA_API_KEY") or "").strip() or None
OLLAMA_MODEL: Final[str] = (os.getenv("OLLAMA_MODEL", "llama3.1").strip() or "llama3.1")
OLLAMA_EMBED_MODEL: Final[str] = (
    os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text").strip() or "nomic-embed-text"
)
RAG_ENABLED: Final[bool] = os.getenv("RAG_ENABLED", "true").strip().lower() in {
    "1",
    "true",
    "yes",
    "y",
    "on",
}
RAG_NEWS_TOP_K: Final[int] = max(
    1,
    int((os.getenv("RAG_NEWS_TOP_K", "6").strip() or "6")),
)
RAG_NEWS_CANDIDATE_POOL: Final[int] = max(
    5,
    int((os.getenv("RAG_NEWS_CANDIDATE_POOL", "15").strip() or "15")),
)

_fb_base_raw = (os.getenv("OLLAMA_API_BASE_FALLBACK") or "").strip().rstrip("/")
OLLAMA_API_BASE_FALLBACK: Final[str | None] = _fb_base_raw or None
OLLAMA_FALLBACK_API_KEY: Final[str | None] = (os.getenv("OLLAMA_FALLBACK_API_KEY") or "").strip() or None
OLLAMA_MODEL_FALLBACK: Final[str] = (
    (os.getenv("OLLAMA_MODEL_FALLBACK") or "").strip() or OLLAMA_MODEL
)

# When a fallback URL is set, the first (primary) request uses this cap so a sleeping PC fails fast (seconds not minutes).
OLLAMA_PRIMARY_TIMEOUT_SEC: Final[int] = max(
    1,
    int((os.getenv("OLLAMA_PRIMARY_TIMEOUT_SEC", "8").strip() or "8")),
)

# Max tokens Ollama may generate for article summaries (/api/generate). Too low cuts mid-sentence.
OLLAMA_SUMMARY_NUM_PREDICT: Final[int] = max(
    128,
    int((os.getenv("OLLAMA_SUMMARY_NUM_PREDICT", "384").strip() or "384")),
)


def _ollama_host_is_loopback(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    return host in ("localhost", "127.0.0.1", "::1") or host.endswith(".localhost")


def ollama_headers_for_endpoint(generate_url: str, *, is_fallback: bool) -> dict[str, str]:
    """
    Auth for /api/generate. Fallback uses OLLAMA_FALLBACK_API_KEY or OLLAMA_API_KEY.
    Primary on localhost/127.0.0.1 sends no Bearer so the same OLLAMA_API_KEY can be cloud-only.
    Primary on any other host uses OLLAMA_API_KEY (single cloud endpoint).
    """
    if is_fallback:
        key = OLLAMA_FALLBACK_API_KEY or OLLAMA_API_KEY
    elif _ollama_host_is_loopback(generate_url):
        return {}
    else:
        key = OLLAMA_API_KEY
    if key:
        return {"Authorization": f"Bearer {key}"}
    return {}


def iter_ollama_generate_targets() -> tuple[tuple[str, dict[str, str], str, bool], ...]:
    """
    Ordered /api/generate targets: (url, headers, model, use_short_timeout_when_fallback_configured).
    """
    primary = (
        OLLAMA_GENERATE_URL,
        ollama_headers_for_endpoint(OLLAMA_GENERATE_URL, is_fallback=False),
        OLLAMA_MODEL,
        True,
    )
    if not OLLAMA_API_BASE_FALLBACK:
        return (primary,)
    fb_url = f"{OLLAMA_API_BASE_FALLBACK}/api/generate"
    secondary = (
        fb_url,
        ollama_headers_for_endpoint(fb_url, is_fallback=True),
        OLLAMA_MODEL_FALLBACK,
        False,
    )
    return (primary, secondary)


def ollama_request_headers() -> dict[str, str]:
    """Same as primary endpoint headers (backward compatible)."""
    return ollama_headers_for_endpoint(OLLAMA_GENERATE_URL, is_fallback=False)


def print_ollama_config_banner() -> None:
    """
    One startup line for deploy logs (Fly, Docker). Per-request routing is not logged.
    """
    if _ollama_host_is_loopback(OLLAMA_GENERATE_URL):
        primary_kind = "this host only (loopback)"
    elif "ollama.com" in (urlparse(OLLAMA_GENERATE_URL).hostname or "").lower():
        primary_kind = "Ollama Cloud"
    else:
        primary_kind = "remote URL"

    parts = [f"primary={_OLLAMA_API_BASE} model={OLLAMA_MODEL} ({primary_kind})"]
    if OLLAMA_API_BASE_FALLBACK:
        parts.append(f"fallback={OLLAMA_API_BASE_FALLBACK} model={OLLAMA_MODEL_FALLBACK}")
    print(f"[ollama] configured: {'; '.join(parts)}", flush=True)

    if (
        _ollama_host_is_loopback(OLLAMA_GENERATE_URL)
        and OLLAMA_API_BASE_FALLBACK
        and (os.getenv("FLY_APP_NAME") or "").strip()
    ):
        print(
            "[ollama] Fly.io: primary localhost is this machine, not your laptop—primary usually "
            "fails and requests use cloud fallback. For local Llama, run the bot on the same PC as "
            "Ollama, or expose Ollama via tunnel/public URL and set OLLAMA_API_BASE.",
            flush=True,
        )


DEFAULT_RSS_FEEDS: Final[List[str]] = [
    "https://www.sarawaktribune.com/feed/",
    "https://news.seehua.com/feed/",
    "https://www.theborneopost.com/feed/",
]


def _normalize_telegram_source_token(raw: str) -> str:
    token = (raw or "").strip().lower()
    if not token:
        return ""
    if token.startswith("@"):
        token = token[1:]
    return token


def _parse_telegram_sources_env() -> List[Tuple[str, str]]:
    """
    Backward-compatible .env parser:
    TELEGRAM_SOURCE_CHANNELS=swbnews,another_channel
    TELEGRAM_SOURCE_SESSION_NAME=sibuwb_session
    """
    session_name = (os.getenv("TELEGRAM_SOURCE_SESSION_NAME") or "sibuwb_session").strip() or "sibuwb_session"
    out: List[Tuple[str, str]] = []
    for part in os.getenv("TELEGRAM_SOURCE_CHANNELS", "").split(","):
        src = _normalize_telegram_source_token(part)
        if not src:
            continue
        out.append((src, session_name))
    return out


def _load_sources_from_file() -> tuple[List[str], List[Tuple[str, str]]]:
    """
    Load RSS + Telegram sources from `RSS_Sources.txt` in the project root.

    File format:
    - One URL per line
    - Blank lines are ignored
    - Lines starting with '#' are ignored
    - Optional Telegram lines:
      - telegram:swbnews
      - telegram:@swbnews|my_session_name
      - TELEGRAM_SOURCE_CHANNELS=swbnews,another_channel
      - TELEGRAM_SOURCE_SESSION_NAME=my_session_name   (default for lines below it)
    """
    # src/core/config.py -> project root is two levels up from `src/`
    project_root = Path(__file__).resolve().parents[2]
    sources_file = project_root / "RSS_Sources.txt"
    if not sources_file.exists():
        return ([], [])

    feeds: List[str] = []
    telegram_pairs: List[Tuple[str, str]] = []
    default_tg_session = "sibuwb_session"
    for raw_line in sources_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, value = line.split("=", 1)
            key = key.strip().upper()
            value = value.strip()
            if key == "TELEGRAM_SOURCE_SESSION_NAME":
                default_tg_session = value or "sibuwb_session"
                continue
            if key == "TELEGRAM_SOURCE_CHANNELS":
                for tok in value.split(","):
                    src = _normalize_telegram_source_token(tok)
                    if not src:
                        continue
                    telegram_pairs.append((src, default_tg_session))
                continue
        if line.lower().startswith("telegram:"):
            payload = line.split(":", 1)[1].strip()
            src_part, session_part = payload, ""
            if "|" in payload:
                src_part, session_part = payload.split("|", 1)
            src = _normalize_telegram_source_token(src_part)
            if not src:
                continue
            sess = (session_part.strip() or default_tg_session or "sibuwb_session")
            telegram_pairs.append((src, sess))
            continue
        if not (line.startswith("http://") or line.startswith("https://")):
            continue
        feeds.append(line)

    # De-duplicate while preserving order.
    unique_feeds = list(dict.fromkeys(feeds))
    unique_tg = list(dict.fromkeys(telegram_pairs))
    return (unique_feeds, unique_tg)


# RSS feeds are loaded from `RSS_Sources.txt` (project root) if present;
# otherwise we fall back to a small default list.
_rss_file_feeds, _rss_file_telegram_sources = _load_sources_from_file()
RSS_FEEDS: Final[List[str]] = _rss_file_feeds or DEFAULT_RSS_FEEDS


def _load_local_interest_keywords() -> tuple[str, ...]:
    """
    Load cheap pre-filter phrases from `Sarawak_Local_Keywords.txt` (project root).
    Empty / missing file means no keyword gating (backward compatible).
    """
    project_root = Path(__file__).resolve().parents[2]
    path = project_root / "Sarawak_Local_Keywords.txt"
    if not path.exists():
        return ()
    out: List[str] = []
    seen: set[str] = set()
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key = line.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
    return tuple(out)


LOCAL_INTEREST_KEYWORDS: Final[tuple[str, ...]] = _load_local_interest_keywords()

# When news_articles has no rows yet, cap RSS entries per feed (avoids huge first pull).
FIRST_BOOT_RSS_MAX_PER_FEED: Final[int] = max(
    1,
    int((os.getenv("FIRST_BOOT_RSS_MAX_PER_FEED", "3").strip() or "3")),
)

# Optional Telegram sources for prefetch (same job as RSS while bot_main is running).
# Requires TELEGRAM_API_ID, TELEGRAM_API_HASH and either:
# - TELEGRAM_SESSION_STRING (recommended on Fly.io: no sqlite3 / .session file). Obtain by running
#   `python test_sibuwb_bot.py` locally after login; the script prints the string at the end.
# - TELEGRAM_PHONE plus an authorized Telethon session file.
#
# Sources can be defined in either:
# - .env: TELEGRAM_SOURCE_CHANNELS + TELEGRAM_SOURCE_SESSION_NAME (backward compatible)
# - RSS_Sources.txt lines (telegram:..., TELEGRAM_SOURCE_CHANNELS=..., TELEGRAM_SOURCE_SESSION_NAME=...)
# If both are set, they are merged and de-duplicated.
TELEGRAM_SOURCE_CONFIGS: Final[List[Tuple[str, str]]] = list(
    dict.fromkeys(_parse_telegram_sources_env() + _rss_file_telegram_sources)
)
TELEGRAM_SOURCE_CHANNELS: Final[List[str]] = list(
    dict.fromkeys([src for src, _session in TELEGRAM_SOURCE_CONFIGS])
)

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

# Cross-source deduplication (same story from different publishers/URLs).
# This runs in-memory during article selection and does not change DB schema.
CROSS_SOURCE_DEDUP_ENABLED: Final[bool] = os.getenv(
    "CROSS_SOURCE_DEDUP_ENABLED", "true"
).strip().lower() in {
    "1",
    "true",
    "yes",
    "y",
    "on",
}
CROSS_SOURCE_DEDUP_DEBUG: Final[bool] = os.getenv(
    "CROSS_SOURCE_DEDUP_DEBUG", "false"
).strip().lower() in {
    "1",
    "true",
    "yes",
    "y",
    "on",
}
CROSS_SOURCE_DEDUP_TITLE_JACCARD_THRESHOLD: Final[float] = min(
    1.0,
    max(
        0.0,
        float(
            (
                os.getenv("CROSS_SOURCE_DEDUP_TITLE_JACCARD_THRESHOLD", "0.82").strip()
                or "0.82"
            )
        ),
    ),
)
CROSS_SOURCE_DEDUP_BODY_JACCARD_THRESHOLD: Final[float] = min(
    1.0,
    max(
        0.0,
        float(
            (
                os.getenv("CROSS_SOURCE_DEDUP_BODY_JACCARD_THRESHOLD", "0.72").strip()
                or "0.72"
            )
        ),
    ),
)
CROSS_SOURCE_DEDUP_MIN_BODY_TOKENS: Final[int] = max(
    8,
    int((os.getenv("CROSS_SOURCE_DEDUP_MIN_BODY_TOKENS", "18").strip() or "18")),
)

# Background RSS prefetch (store to DB while bot is running)
PREFETCH_ENABLED: Final[bool] = os.getenv("PREFETCH_ENABLED", "true").strip().lower() in {
    "1",
    "true",
    "yes",
    "y",
    "on",
}

# Scheduled digest only after this many minutes from user.first_seen_at (set on /start).
# Lets new users explore /settings without an immediate push. Set to 0 to disable.
SCHEDULED_PUSH_GRACE_MINUTES_AFTER_FIRST_SEEN: Final[int] = max(
    0,
    int((os.getenv("SCHEDULED_PUSH_GRACE_MINUTES_AFTER_FIRST_SEEN", "30").strip() or "30")),
)

# Scheduled pushes only: no digests during this local wall-clock window (default 12am–6am Malaysia time).
# Does not affect /latest, /testpush, or urgent channel alerts.
SCHEDULED_PUSH_QUIET_HOURS_ENABLED: Final[bool] = os.getenv(
    "SCHEDULED_PUSH_QUIET_HOURS_ENABLED", "true"
).strip().lower() in {"1", "true", "yes", "y", "on"}
SCHEDULED_PUSH_QUIET_TIMEZONE: Final[str] = (
    (os.getenv("SCHEDULED_PUSH_QUIET_TIMEZONE", "Asia/Kuching") or "Asia/Kuching").strip()
)


def _env_hour(key: str, default: int) -> int:
    try:
        v = int((os.getenv(key, str(default)) or str(default)).strip())
    except ValueError:
        return default
    return max(0, min(23, v))


SCHEDULED_PUSH_QUIET_START_HOUR_LOCAL: Final[int] = _env_hour(
    "SCHEDULED_PUSH_QUIET_START_HOUR", 0
)
SCHEDULED_PUSH_QUIET_END_HOUR_LOCAL: Final[int] = _env_hour(
    "SCHEDULED_PUSH_QUIET_END_HOUR", 6
)

# Digest mode local send times (Track A).
DIGEST_MORNING_HOUR_LOCAL: Final[int] = _env_hour("DIGEST_MORNING_HOUR", 7)
DIGEST_EVENING_HOUR_LOCAL: Final[int] = _env_hour("DIGEST_EVENING_HOUR", 20)
# Scheduler poll window to fire digest slots safely (job runs every 60s by default).
DIGEST_TRIGGER_WINDOW_MINUTES: Final[int] = max(
    1,
    min(30, int((os.getenv("DIGEST_TRIGGER_WINDOW_MINUTES", "10").strip() or "10"))),
)


def is_scheduled_push_quiet_hours_now() -> bool:
    """
    True if scheduled news pushes should be skipped. Window is [start, end) in local hours;
    if start > end, the quiet period wraps past midnight (e.g. 22–6).
    """
    if not SCHEDULED_PUSH_QUIET_HOURS_ENABLED:
        return False
    s, e = SCHEDULED_PUSH_QUIET_START_HOUR_LOCAL, SCHEDULED_PUSH_QUIET_END_HOUR_LOCAL
    if s == e:
        return False
    try:
        from zoneinfo import ZoneInfo

        tz = ZoneInfo(SCHEDULED_PUSH_QUIET_TIMEZONE)
    except Exception:
        return False
    h = datetime.now(tz).hour
    if s < e:
        return s <= h < e
    return h >= s or h < e


# Global RSS + Telegram fetch interval (minutes): ONE schedule for the whole app, not per user.
# Default 15. Per-user notification timing is user_preferences.frequency (/settings) in bot_main.
# Optional: PREFETCH_INTERVAL_MINUTES=60 for hourly fetch.
PREFETCH_INTERVAL_MINUTES: Final[int] = max(
    1,
    int((os.getenv("PREFETCH_INTERVAL_MINUTES", "15").strip() or "15")),
)

# After each prefetch insert, call Ollama to fill news_articles.ai_summary (same pipeline as user delivery).
# Set PREFETCH_AI_SUMMARY=false to skip (saves API quota / latency on insert).
PREFETCH_AI_SUMMARY: Final[bool] = os.getenv("PREFETCH_AI_SUMMARY", "true").strip().lower() in {
    "1",
    "true",
    "yes",
    "y",
    "on",
}

# DB retention cleanup (old news + delivery rows).
DB_CLEANUP_ENABLED: Final[bool] = os.getenv("DB_CLEANUP_ENABLED", "true").strip().lower() in {
    "1",
    "true",
    "yes",
    "y",
    "on",
}
DB_RETENTION_DAYS: Final[int] = max(
    1,
    int((os.getenv("DB_RETENTION_DAYS", "30").strip() or "30")),
)
DB_CLEANUP_INTERVAL_HOURS: Final[int] = max(
    1,
    int((os.getenv("DB_CLEANUP_INTERVAL_HOURS", "24").strip() or "24")),
)


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


