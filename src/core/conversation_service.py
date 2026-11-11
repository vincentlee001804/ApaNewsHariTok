from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Iterable

from sqlalchemy import delete, select

from src.core.config import (
    CONVERSATION_MAX_AGE_HOURS,
    CONVERSATION_MAX_TURNS,
    CONVERSATION_MEMORY_ENABLED,
)
from src.core.location_extractor import SARAWAK_LOCATION_ALIASES
from src.core.models import ConversationMessage, User
from src.core.news_categories import NEWS_ARTICLE_CATEGORY_LABELS
from src.storage.database import SessionLocal

logger = logging.getLogger(__name__)

# Follow-up markers that suggest the user is continuing the previous topic.
_FOLLOWUP_MARKERS: set[str] = {
    "what about",
    "how about",
    "tell me more",
    "more details",
    "more info",
    "any more",
    "why",
    "when",
    "where",
    "who",
    "how",
    "is it",
    "about it",
    "that one",
    "the one",
    "those",
    "they",
    "them",
    "it",
    "this",
    "that",
    # Bahasa Malaysia continuation markers
    "macam mana",
    "bagaimana",
    "apa kata",
    "kenapa",
    "bila",
    "di mana",
    "pula",
}

# Additional topic words not covered by category labels (common Sarawak news subjects).
_TOPIC_KEYWORDS: set[str] = {
    "flood",
    "floods",
    "flooding",
    "landslide",
    "power outage",
    "blackout",
    "water outage",
    "water disruption",
    "pipe burst",
    "accident",
    "accidents",
    "road accident",
    "crash",
    "crashed",
    "collision",
    "traffic jam",
    "road closure",
    "fire",
    "haze",
    "covid",
    "dengue",
    "election",
    "by-election",
    "budget",
    "subsidy",
    "grant",
    "tech",
    "renewable energy",
    "green energy",
    # Bahasa Malaysia topic words
    "banjir",
    "kebakaran",
    "kemalangan",
    "kesihatan",
    "politik",
    "sukan",
    "teknologi",
    "cuaca",
    "pendidikan",
}

# Words that never constitute a topic even when nothing else remains
# (auxiliary verbs, pronouns, BM question words, greeting filler, and
# meta/system words so 'how many users are in your system?' is not treated
# as a news topic and reaches the answering LLM instead of the topic gate).
_NON_TOPIC_WORDS: set[str] = {
    "did", "does", "do", "done", "happen", "happened", "happens",
    "one", "ones", "going", "got", "get", "getting", "please",
    "macam", "mana", "pula", "apakah", "yang", "untuk", "dengan",
    "ada", "boleh", "sudah", "akan",
    "related", "stories", "story", "window", "current", "optional",
    "hello", "hi", "hey", "hai", "there",
    "morning", "afternoon", "evening", "night",
    "selamat", "pagi", "petang", "malam",
    "many", "much", "user", "users", "your", "yours", "ur",
    "system", "api", "key", "keys", "password", "token",
}

# Leading greetings to strip before extracting a topic from user text.
_GREETING_PATTERNS: tuple[str, ...] = (
    r"^hello[,.!?]?\s+",
    r"^hi[,.!?]?\s+",
    r"^hey[,.!?]?\s+",
    r"^hai[,.!?]?\s+",
    r"^good morning[,.!?]?\s+",
    r"^good afternoon[,.!?]?\s+",
    r"^good evening[,.!?]?\s+",
    r"^good night[,.!?]?\s+",
)


def _strip_leading_greetings(text: str) -> str:
    """Remove common greeting prefixes so they are not mistaken for topics."""
    cleaned = (text or "").strip()
    lowered = cleaned.lower()
    for pattern in _GREETING_PATTERNS:
        cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE)
    # If nothing left after stripping, return original so follow-up logic still works.
    if not cleaned.strip():
        return (text or "").strip()
    return cleaned.strip()


def _lowercase_set(items: Iterable[str]) -> set[str]:
    return {item.strip().lower() for item in items if item and item.strip()}


# Generic labels like "Local" / "General" are too broad to be useful follow-up topics.
_TOPIC_TOKENS: set[str] = (
    _lowercase_set(c for c in NEWS_ARTICLE_CATEGORY_LABELS if c not in {"Local", "General"})
    | _TOPIC_KEYWORDS
)


def save_conversation_turn(
    telegram_id: int,
    role: str,
    content: str,
    interaction_type: str | None = None,
    shown_article_links: list[str] | None = None,
    utility_alert_id: int | None = None,
) -> None:
    """
    Persist one conversation turn. Does nothing when conversation memory is disabled.
    Also prunes old rows for this user so the table stays small.

    shown_article_links: article links displayed in this turn (assistant turns
    only); later turns in the same conversation exclude them to avoid repeats.
    utility_alert_id: for utility_alert turns, the UtilityAlert this turn pushed.
    """
    if not CONVERSATION_MEMORY_ENABLED:
        return
    if not content or not content.strip():
        return
    role = (role or "").strip().lower()
    if role not in {"user", "assistant"}:
        return

    cleaned_links: list[str] | None = None
    if shown_article_links:
        seen: set[str] = set()
        cleaned_links = []
        for link in shown_article_links:
            link = (link or "").strip()
            if link and link not in seen:
                seen.add(link)
                cleaned_links.append(link)
        cleaned_links = cleaned_links[:50] or None

    try:
        with SessionLocal() as session:
            user = session.execute(
                select(User).where(User.telegram_id == telegram_id)
            ).scalar_one_or_none()
            if not user:
                return

            session.add(
                ConversationMessage(
                    user_id=user.id,
                    role=role,
                    content=content.strip()[:4000],
                    interaction_type=(interaction_type or "")[:32] or None,
                    shown_article_links=cleaned_links,
                    utility_alert_id=utility_alert_id,
                    created_at=datetime.utcnow(),
                )
            )
            session.commit()

            # Prune: keep only recent turns and recent age.
            _prune_conversation_history(session, user.id)
            session.commit()
    except Exception:
        logger.exception("[conversation] failed to save turn for telegram_id=%s", telegram_id)


def _prune_conversation_history(session, user_id: int) -> None:
    """Delete old and excess conversation rows for a user."""
    max_rows = max(1, CONVERSATION_MAX_TURNS) * 2
    age_cutoff = datetime.utcnow() - timedelta(hours=max(1, CONVERSATION_MAX_AGE_HOURS))

    session.execute(
        delete(ConversationMessage).where(
            (ConversationMessage.user_id == user_id)
            & (ConversationMessage.created_at < age_cutoff)
        )
    )

    # If still too many rows, delete oldest first.
    rows = session.execute(
        select(ConversationMessage.id)
        .where(ConversationMessage.user_id == user_id)
        .order_by(ConversationMessage.created_at.desc())
    ).scalars().all()

    if len(rows) > max_rows:
        excess = rows[max_rows:]
        if excess:
            session.execute(
                delete(ConversationMessage).where(ConversationMessage.id.in_(excess))
            )


def get_recent_conversation_turns(
    telegram_id: int,
    max_turns: int | None = None,
    max_age_hours: int | None = None,
) -> list[dict[str, str]]:
    """
    Return recent conversation turns as chronologically-ordered dicts:
    [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}, ...]
    """
    if not CONVERSATION_MEMORY_ENABLED:
        return []

    turns = max_turns if max_turns is not None else CONVERSATION_MAX_TURNS
    turns = max(1, turns)
    age = max_age_hours if max_age_hours is not None else CONVERSATION_MAX_AGE_HOURS
    age = max(1, age)
    cutoff = datetime.utcnow() - timedelta(hours=age)
    limit = turns * 2

    try:
        with SessionLocal() as session:
            user = session.execute(
                select(User).where(User.telegram_id == telegram_id)
            ).scalar_one_or_none()
            if not user:
                return []

            rows = session.execute(
                select(ConversationMessage)
                .where(
                    (ConversationMessage.user_id == user.id)
                    & (ConversationMessage.created_at >= cutoff)
                )
                .order_by(ConversationMessage.created_at.desc())
                .limit(limit)
            ).scalars().all()

            return [
                {
                    "role": row.role,
                    "content": row.content,
                    "interaction_type": row.interaction_type or "",
                    "shown_article_links": list(row.shown_article_links or []),
                    "utility_alert_id": row.utility_alert_id,
                }
                for row in reversed(rows)
            ]
    except Exception:
        logger.exception("[conversation] failed to load history for telegram_id=%s", telegram_id)
        return []


def _extract_locations(text: str) -> set[str]:
    """Return canonical Sarawak locations mentioned anywhere in the text."""
    text = (text or "").lower()
    found: set[str] = set()
    for canonical, aliases in SARAWAK_LOCATION_ALIASES.items():
        for alias in aliases:
            alias_token = alias.strip().lower()
            if not alias_token:
                continue
            if re.search(rf"\b{re.escape(alias_token)}\b", text):
                found.add(canonical)
                break
    return found


def _extract_failed_topic_from_response(text: str) -> str | None:
    """
    When the previous assistant response was a 'no + suggest' response,
    extract the topic from the phrase 'I couldn't find <topic> news...'.
    This is more reliable than generic topic extraction because suggestions
    in the response can confuse the generic extractor.
    """
    lowered = (text or "").lower()
    m = re.search(r"i couldn't find ([\w\s]+?) news\b", lowered)
    if m:
        topic = m.group(1).strip()
        # Refusal boilerplate is not a topic ("I couldn't find related news..."),
        # and a long match means the phrase ran into LLM answer prose
        # ("...any relevant information about an accident in Bintulu in the
        # provided news") — real missed topics are 1-4 words.
        if topic and topic not in {"related", "any", "some", "other"} and len(topic.split()) <= 4:
            return topic
    return None


# Ordinal references to items in the assistant's last displayed list, e.g.
# "explain more about the first news" or "berita kedua". Value is the 0-based
# index into that list; -1 means the last item.
_ORDINAL_INDEX: dict[str, int] = {
    "first": 0, "1st": 0,
    "second": 1, "2nd": 1,
    "third": 2, "3rd": 2,
    "fourth": 3, "4th": 3,
    "fifth": 4, "5th": 4,
    "last": -1,
}

_BM_ORDINAL_INDEX: dict[str, int] = {
    "pertama": 0, "kedua": 1, "ketiga": 2, "keempat": 3, "kelima": 4,
    "terakhir": -1, "akhir": -1,
}

# English ordinal precedes the noun: "the first news", "the 2nd story".
_ORDINAL_NOUN_RE = re.compile(
    r"\b(first|second|third|fourth|fifth|1st|2nd|3rd|4th|5th|last)\s+"
    r"(?:news|one|ones|story|stories|item|items|article|articles|headline|headlines)\b",
    re.IGNORECASE,
)

# BM ordinal follows the noun: "berita pertama", "tajuk kedua", "yang terakhir".
_BM_ORDINAL_RE = re.compile(
    r"\b(?:berita|tajuk|artikel|yang)\s+"
    r"(pertama|kedua|ketiga|keempat|kelima|terakhir|akhir)\b",
    re.IGNORECASE,
)


def extract_ordinal_reference(query: str) -> int | None:
    """Detect ordinal references like "the first news" / "berita kedua" / "the last one".

    Returns a 0-based index into the last displayed article list, -1 for "last",
    or None when the query has no ordinal reference.
    """
    text = (query or "").strip()
    if not text:
        return None
    m = _ORDINAL_NOUN_RE.search(text)
    if m:
        return _ORDINAL_INDEX.get(m.group(1).lower())
    m = _BM_ORDINAL_RE.search(text)
    if m:
        return _BM_ORDINAL_INDEX.get(m.group(1).lower())
    return None


# Meta/out-of-scope questions that should never reach retrieval or the LLM:
# requests for secrets/credentials/system info and bot-identity questions.
# The handler answers these with a fixed refusal (no cards, no DB query).
_OUT_OF_SCOPE_RE = re.compile(
    r"\b(api[\s-]?key|apikey|password|passwd|secret key|api token|access token|"
    r"system prompt|your (system|database|server|code|source code)|"
    r"how many users|number of users|user count|"
    r"who (made|created|built|developed) you|who are you|about yourself)\b",
    re.IGNORECASE,
)


def is_out_of_scope_query(query: str) -> bool:
    """True for questions about the bot itself, its system, or its secrets —
    these get a fixed refusal instead of a news-database search."""
    return bool(_OUT_OF_SCOPE_RE.search((query or "").strip()))


def _extract_topic(text: str) -> str | None:
    """
    Best-effort topic extraction from user query or assistant response.
    Prefers category labels / common news topic keywords. Ignores location names.
    Strips leading greetings so "Hello. Any tech news" does not become topic "hello".
    """
    text = _strip_leading_greetings(text or "").strip()
    if not text:
        return None

    # Strip echoed "[Category]" tags from formatted bot messages so they are
    # not mistaken for the user's topic when mining assistant history.
    text = re.sub(
        r"\[\s*(?:" + "|".join(re.escape(c) for c in NEWS_ARTICLE_CATEGORY_LABELS) + r")\s*\]",
        " ",
        text,
        flags=re.IGNORECASE,
    )
    lowered = text.lower()

    # Prefer explicit category / topic keywords.
    for token in sorted(_TOPIC_TOKENS, key=len, reverse=True):
        if re.search(rf"\b{re.escape(token)}\b", lowered):
            return token

    # Fallback: take the first substantive phrase by dropping common question words
    # and any location aliases. Only return a topic if at least one remaining word
    # is a known topic/category keyword or a reasonably specific noun.
    location_tokens: set[str] = set()
    for aliases in SARAWAK_LOCATION_ALIASES.values():
        location_tokens.update(a.strip().lower() for a in aliases if a.strip())

    cleaned = re.sub(r"[?!.,;:]", " ", lowered)
    stop_words = {
        "a", "an", "the", "any", "some", "more", "about", "in", "on", "at", "for",
        "of", "with", "to", "from", "and", "or", "is", "are", "was", "were",
        "there", "here", "today", "latest", "news", "summary", "ringkasan", "berita",
        "tell", "me", "give", "show", "what", "how", "when", "where", "who", "why",
        "this", "that", "these", "those", "it", "they", "them",
    } | _NON_TOPIC_WORDS
    words = [
        w
        for w in cleaned.split()
        if w and w not in stop_words and w not in location_tokens
    ]
    if not words:
        return None

    # If the only remaining word is a single generic word, ignore it.
    if len(words) == 1 and len(words[0]) <= 3:
        return None

    # If the phrase contains a known topic/category word, use the whole phrase.
    phrase = " ".join(words[:3])
    if any(token in phrase for token in _TOPIC_TOKENS):
        return phrase

    # Otherwise, only return a topic if it looks like a specific multi-word phrase.
    if len(words) >= 2:
        return phrase
    return None


def _is_greeting_only(text: str) -> bool:
    """True when the text is just greeting filler with no content words
    (e.g. 'hello there', 'selamat petang'). Such messages are handled by the
    handler's greeting shortcut, or should fall through — never expanded as
    follow-ups that inherit a stale topic."""
    cleaned = re.sub(r"[?!.,;:]", " ", (text or "").lower())
    words = [w for w in cleaned.split() if w]
    if not words:
        return False
    return all(w in _NON_TOPIC_WORDS for w in words)


def is_followup_query(query: str, history: list[dict[str, str]]) -> bool:
    """
    Heuristic: is this query likely a continuation of the previous turn?
    """
    if not history:
        return False

    query = _strip_leading_greetings(query or "").strip().lower()
    if not query:
        return False

    if _is_greeting_only(query):
        return False

    # Explicitly self-contained queries are not follow-ups.
    word_count = len(query.split())
    if word_count >= 7:
        return False

    # If it names a location or topic clearly, it may still be a follow-up,
    # but we let expand_followup_query decide how to combine it with history.
    current_locations = _extract_locations(query)
    current_topic = _extract_topic(query)

    # Very short and vague is a follow-up.
    if word_count <= 3 and not current_locations and not current_topic:
        return True

    # Follow-up markers strongly indicate continuation.
    padded = f" {query} "
    if any(f" {marker} " in padded for marker in _FOLLOWUP_MARKERS):
        return True

    # If the query only adds a location or only adds a topic, treat as follow-up.
    if (current_locations and not current_topic) or (current_topic and not current_locations):
        return True

    return False


def expand_followup_query(
    query: str,
    history: list[dict[str, str]],
) -> tuple[str, str | None]:
    """
    Expand a vague follow-up using the previous assistant response.
    Returns (expanded_query, inferred_interaction_type).
    """
    query = _strip_leading_greetings(query or "").strip()
    if not query or not history:
        return query, None

    if not is_followup_query(query, history):
        return query, None

    last_assistant = None
    last_user = None
    for turn in reversed(history):
        if last_assistant is None and turn.get("role") == "assistant":
            last_assistant = turn
        elif last_user is None and turn.get("role") == "user":
            last_user = turn
        if last_assistant and last_user:
            break

    if not last_assistant:
        return query, None

    prev_content = last_assistant.get("content", "")
    prev_user_content = last_user.get("content", "")
    # Prefer the topic from the user's previous question. Assistant prose can
    # contain category words in unrelated contexts (e.g., "learning environment"),
    # which confuses generic topic extraction.
    prev_topic = (
        _extract_topic(prev_user_content)
        or _extract_failed_topic_from_response(prev_content)
        or _extract_topic(prev_content)
    )
    prev_locations = _extract_locations(prev_content) | _extract_locations(prev_user_content)

    current_locations = _extract_locations(query)
    current_topic = _extract_topic(query)

    # Determine final topic/location.
    topic = current_topic or prev_topic
    locations = current_locations or prev_locations

    if not topic and not locations:
        return query, last_assistant.get("interaction_type")

    # Build expanded query.
    parts: list[str] = []
    lowered = query.lower()

    # Handle "tell me more / more details" explicitly.
    if any(marker in lowered for marker in {"tell me more", "more details", "more info", "any more"}):
        parts.append("more details about")
    elif lowered.startswith("why ") or lowered.startswith("when ") or lowered.startswith("where "):
        parts.append(lowered.split()[0])  # why / when / where
        parts.append("about")

    if topic:
        parts.append(topic)
    if locations:
        parts.append("in")
        parts.append(", ".join(sorted(locations)))

    expanded = " ".join(parts).strip()
    # Avoid replacing the query with something worse; keep original if expansion is empty.
    if not expanded or expanded.lower() == query.lower():
        return query, last_assistant.get("interaction_type")

    return expanded, last_assistant.get("interaction_type")


def get_last_interaction_type(history: list[dict[str, str]]) -> str | None:
    """Return the interaction_type of the most recent assistant turn, if any."""
    for turn in reversed(history):
        if turn.get("role") == "assistant":
            return turn.get("interaction_type") or None
    return None


# Words that scope a follow-up to the pushed utility alert rather than to the
# news database (English + Bahasa Malaysia alert-intent vocabulary).
_ALERT_SCOPE_MARKERS: tuple[str, ...] = (
    # duration / timing
    "how long", "when", "until", "till", "what time", "bila",
    "berapa lama", "sehingga",
    # affected area
    "which area", "where", "kawasan", "di mana", "lokasi",
    "affected", "area", "location",
    # restoration status
    "restored", "restore", "resume", "back to normal", "still no",
    "status", "pulih", "sudah", "ok now",
    # more details
    "more", "detail", "details", "full notice", "lagi", "maklumat",
)


def resolve_utility_alert_followup(
    query: str, history: list[dict[str, str]]
) -> int | None:
    """
    If the most recent assistant turn pushed a utility alert and this query is
    scoped to that alert, return the UtilityAlert id so the handler can answer
    deterministically from the notice instead of searching news.

    Queries carrying a fresh news topic or a location are left to normal news Q&A.
    """
    if not history:
        return None
    lowered = (query or "").strip().lower()
    if not lowered:
        return None

    alert_id: int | None = None
    for turn in reversed(history):
        if turn.get("role") == "assistant":
            if turn.get("interaction_type") == "utility_alert":
                alert_id = turn.get("utility_alert_id")
            break
    if not alert_id:
        return None

    # Alert-intent wording wins — "how long will the water disruption last?"
    # must not fall through to news search just because it says "disruption".
    if any(marker in lowered for marker in _ALERT_SCOPE_MARKERS):
        return alert_id
    # Fresh news topic or a location swap ("how about Miri?") -> news Q&A.
    if _extract_topic(query):
        return None
    if _extract_locations(query):
        return None
    # Vague short follow-up right after an alert push ("tell me more") -> alert.
    if is_followup_query(query, history):
        return alert_id
    return None
