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
}

# Additional topic words not covered by category labels (common Sarawak news subjects).
_TOPIC_KEYWORDS: set[str] = {
    "flood",
    "floods",
    "flooding",
    " landslide",
    "power outage",
    "blackout",
    "water outage",
    "water disruption",
    "pipe burst",
    "accident",
    "crash",
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
}


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
) -> None:
    """
    Persist one conversation turn. Does nothing when conversation memory is disabled.
    Also prunes old rows for this user so the table stays small.
    """
    if not CONVERSATION_MEMORY_ENABLED:
        return
    if not content or not content.strip():
        return
    role = (role or "").strip().lower()
    if role not in {"user", "assistant"}:
        return

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


def _extract_topic(text: str) -> str | None:
    """
    Best-effort topic extraction from user query or assistant response.
    Prefers category labels / common news topic keywords. Ignores location names.
    """
    text = (text or "").strip()
    if not text:
        return None

    lowered = text.lower()

    # Prefer explicit category / topic keywords.
    for token in sorted(_TOPIC_TOKENS, key=len, reverse=True):
        if re.search(rf"\b{re.escape(token)}\b", lowered):
            return token

    # Fallback: take the first substantive phrase by dropping common question words
    # and any location aliases.
    location_tokens: set[str] = set()
    for aliases in SARAWAK_LOCATION_ALIASES.values():
        location_tokens.update(a.strip().lower() for a in aliases if a.strip())

    cleaned = re.sub(r"[?!.,;:]", " ", lowered)
    stop_words = {
        "a", "an", "the", "any", "some", "more", "about", "in", "on", "at", "for",
        "of", "with", "to", "from", "and", "or", "is", "are", "was", "were",
        "there", "here", "today", "latest", "news", "summary", "ringkasan", "berita",
        "tell", "me", "give", "show", "what", "how", "when", "where", "who", "why",
    }
    words = [
        w
        for w in cleaned.split()
        if w and w not in stop_words and w not in location_tokens
    ]
    if words:
        # Return up to first 3 meaningful words as a compact topic phrase.
        return " ".join(words[:3])
    return None


def is_followup_query(query: str, history: list[dict[str, str]]) -> bool:
    """
    Heuristic: is this query likely a continuation of the previous turn?
    """
    if not history:
        return False

    query = (query or "").strip().lower()
    if not query:
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
    query = (query or "").strip()
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
    prev_topic = _extract_topic(prev_content) or _extract_topic(last_user.get("content", ""))
    prev_locations = _extract_locations(prev_content)

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
