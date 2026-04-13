from __future__ import annotations

import re
from typing import Optional, Tuple


# Canonical Sarawak location keys must match `src/bot/handlers.py`'s location_map keys
# so `UserPreference.locations` and `NewsArticle.location` align.
SARAWAK_LOCATION_ALIASES: dict[str, list[str]] = {
    "kota samarahan": ["kota samarahan", "samarahan"],
    "kuching": ["kuching"],
    "miri": ["miri"],
    "sibu": ["sibu"],
    "bintulu": ["bintulu"],
    "serian": ["serian"],
    "sarikei": ["sarikei"],
    "sri aman": ["sri aman", "sriaman"],
    "mukah": ["mukah"],
    "limbang": ["limbang"],
    "lawas": ["lawas"],
    "betong": ["betong"],
    "saratok": ["saratok"],
    "kapit": ["kapit"],
    "marudi": ["marudi"],
    "belaga": ["belaga"],
}


def _starts_with_any_alias(title_lower: str) -> Optional[str]:
    """
    Return canonical location if the title begins with a known Sarawak location/alias.
    """
    # Headlines often look like: "KUCHING: ...", "Kota Samarahan (March 7): ...", etc.
    # So we check for prefix + word boundary.
    for canonical, aliases in SARAWAK_LOCATION_ALIASES.items():
        for alias in aliases:
            alias_lower = alias.lower().strip()
            if not alias_lower:
                continue
            # Match: "<alias>" followed by word boundary OR punctuation like ':' '(' '-' '–'
            if re.match(rf"^{re.escape(alias_lower)}(\b|[\s:\(\-–—])", title_lower):
                return canonical
    return None


def _first_alias_mention(text_lower: str) -> Optional[str]:
    """
    Return canonical location for the earliest whole-word alias mention in text.
    Prefer explicit mentions in title/body over dictionary order.
    """
    if not text_lower:
        return None

    best: tuple[int, str] | None = None
    for canonical, aliases in SARAWAK_LOCATION_ALIASES.items():
        for alias in aliases:
            token = alias.lower().strip()
            if not token:
                continue
            m = re.search(rf"\b{re.escape(token)}\b", text_lower)
            if not m:
                continue
            idx = m.start()
            if best is None or idx < best[0]:
                best = (idx, canonical)
    return best[1] if best else None


def extract_location_and_state(title: str, text: str | None = None) -> Tuple[Optional[str], str]:
    """
    Extract a coarse location for prioritization.

    - If the title starts with a known Sarawak city/region, return:
        (canonical_sarawak_location, "sarawak")
    - Otherwise return:
        (None, "other")

    This is intentionally conservative for non-Sarawak to avoid false positives.
    """
    title_lower = (title or "").strip().lower()
    if not title_lower:
        return None, "other"

    canonical = _starts_with_any_alias(title_lower)
    if canonical:
        return canonical, "sarawak"

    # Next priority: explicit location mention anywhere in title.
    # This avoids body-text noise overriding a clear city in the headline.
    canonical = _first_alias_mention(title_lower)
    if canonical:
        return canonical, "sarawak"

    # Last fallback: body text.
    combined = (text or "").lower()
    canonical = _first_alias_mention(combined)
    if canonical:
        return canonical, "sarawak"

    return None, "other"

