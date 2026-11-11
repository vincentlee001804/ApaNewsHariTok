"""
Single taxonomy for news article categories (DB `news_articles.category`, Ollama classify, /settings).

Stored labels use Title Case (e.g. "Politics"). User preferences use lowercase tokens (e.g. politics).
"""
from __future__ import annotations

from typing import Final

# Order: specific first for keyword fallback in services._extract_category (Local/General last in that function).
NEWS_ARTICLE_CATEGORY_LABELS: Final[tuple[str, ...]] = (
    "Emergency",
    "Health",
    "Sports",
    "Politics",
    "Education",
    "Business",
    "Technology",
    "Entertainment",
    "Environment",
    "Infrastructure",
    "Crime",
    "Social",
    "Tourism",
    "Food",
    "Agriculture",
    "Transport",
    "Weather",
    "Culture",
    "Religion",
    "Consumer",
    "Housing",
    "Defence",
    "Local",
    "General",
)

_LABEL_LOWER: Final[dict[str, str]] = {c.lower(): c for c in NEWS_ARTICLE_CATEGORY_LABELS}

# Common model misspellings / variants -> canonical label
_CATEGORY_ALIASES: Final[dict[str, str]] = {
    "political": "Politics",
    "politics.": "Politics",
    "sport": "Sports",
    "tech": "Technology",
    "it": "Technology",
    "crime/law": "Crime",
    "legal": "Crime",
    "traffic": "Transport",
    "aviation": "Transport",
    "farming": "Agriculture",
    "agricultural": "Agriculture",
    "cultural": "Culture",
    "religious": "Religion",
    "defense": "Defence",
    "military": "Defence",
    "weather-related": "Weather",
    "meteorology": "Weather",
    "retail": "Consumer",
    "economy": "Business",
    "property": "Housing",
    "real estate": "Housing",
}


def normalize_llm_category_token(raw: str) -> str | None:
    """
    Map free-form model output to a canonical NEWS_ARTICLE_CATEGORY_LABELS value, or None.
    """
    if not raw:
        return None
    s = raw.strip().strip('"').strip("'").splitlines()[0].strip()
    if not s:
        return None
    if s in NEWS_ARTICLE_CATEGORY_LABELS:
        return s
    low = s.lower()
    if low in _LABEL_LOWER:
        return _LABEL_LOWER[low]
    if low in _CATEGORY_ALIASES:
        return _CATEGORY_ALIASES[low]
    # Prefix / fuzzy: first token
    first = low.split()[0].rstrip(".,;:!?")
    if first in _LABEL_LOWER:
        return _LABEL_LOWER[first]
    if first in _CATEGORY_ALIASES:
        return _CATEGORY_ALIASES[first]
    return None


def category_labels_for_llm_prompt() -> str:
    return ", ".join(NEWS_ARTICLE_CATEGORY_LABELS)


def slug_for_callback(label: str) -> str:
    """callback_data suffix: cat_<slug> (lowercase, no spaces)."""
    return label.strip().lower()


def label_from_slug(slug: str) -> str | None:
    return _LABEL_LOWER.get((slug or "").strip().lower())


# Canonical topic -> equivalent surface forms (English synonyms + Bahasa Malaysia).
# Used by the news agent to match user phrasing against article text so that
# e.g. "accident" matches a "collision" headline and "banjir" matches "flood".
TOPIC_SYNONYMS: Final[dict[str, tuple[str, ...]]] = {
    "accident": ("accident", "accidents", "crash", "crashed", "collision", "wreck", "overturn", "overturned", "kemalangan", "terbabas"),
    "flood": ("flood", "floods", "flooding", "banjir", "kebanjiran"),
    "fire": ("fire", "kebakaran", "terbakar", "burning"),
    "renewable energy": ("renewable energy", "renewable", "green energy", "hydrogen", "hidrogen", "solar", "tenaga boleh baharu"),
    "tech": ("tech", "technology", "teknologi", "digital", "AI", "ICT"),
}

# Flattened reverse lookup: surface form (lowercase) -> canonical topic.
_TOPIC_FORM_TO_CANONICAL: Final[dict[str, str]] = {
    form.lower(): canonical
    for canonical, forms in TOPIC_SYNONYMS.items()
    for form in forms
}


def canonical_topic_for(form: str) -> str | None:
    """Return the canonical topic for a surface form (e.g. 'banjir' -> 'flood')."""
    if not form:
        return None
    return _TOPIC_FORM_TO_CANONICAL.get(form.strip().lower())


def topic_surface_forms(topic: str) -> tuple[str, ...]:
    """All surface forms for a topic (canonical + synonyms); unknown topics return themselves."""
    if not topic:
        return ()
    canonical = canonical_topic_for(topic)
    if canonical:
        return TOPIC_SYNONYMS[canonical]
    return (topic.strip().lower(),)


def replace_topic_forms_in_text(text: str) -> str:
    """Rewrite known BM/synonym surface forms in a query to the canonical English form.

    Only whole words are replaced, so retrieval, the topic gate, and the LLM all
    see e.g. 'flood' even when the user typed 'banjir'. Unknown text passes through.
    """
    import re

    if not text:
        return text
    lowered = text.lower()

    def _repl(m: re.Match) -> str:
        return _TOPIC_FORM_TO_CANONICAL[m.group(0).lower()]

    pattern = r"\b(" + "|".join(re.escape(f) for f in sorted(_TOPIC_FORM_TO_CANONICAL, key=len, reverse=True)) + r")\b"
    return re.sub(pattern, _repl, text, flags=re.IGNORECASE)
