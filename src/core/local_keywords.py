from __future__ import annotations

from src.core.config import LOCAL_INTEREST_KEYWORDS


def local_keyword_filter_enabled() -> bool:
    return bool(LOCAL_INTEREST_KEYWORDS)


def matches_local_interest(title: str, summary: str | None) -> bool:
    """
    Cheap pre-filter before Ollama. If LOCAL_INTEREST_KEYWORDS is empty, all items pass.
    Otherwise at least one keyword must appear as a substring in title or summary (case-insensitive).
    """
    if not LOCAL_INTEREST_KEYWORDS:
        return True
    blob = f"{title or ''}\n{summary or ''}".lower()
    return any(k in blob for k in LOCAL_INTEREST_KEYWORDS)
