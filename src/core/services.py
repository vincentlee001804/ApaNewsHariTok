from __future__ import annotations

from datetime import datetime, timedelta
import logging
import re
from typing import Any, Final, List

from sqlalchemy import exists, func, select

from src.ai.summarizer import (
    classify_category,
    clip_plain_text_to_word_limit,
    normalize_stored_ai_summary,
    strip_markdown_artifacts_for_plain_text,
    summarize,
    waze_alerts_to_news_sentences,
)
from src.core.config import (
    CROSS_SOURCE_DEDUP_BODY_JACCARD_THRESHOLD,
    CROSS_SOURCE_DEDUP_DEBUG,
    CROSS_SOURCE_DEDUP_ENABLED,
    CROSS_SOURCE_DEDUP_MIN_BODY_TOKENS,
    CROSS_SOURCE_DEDUP_TITLE_JACCARD_THRESHOLD,
    DEDUPLICATION_ENABLED,
    RAG_ENABLED,
    RAG_NEWS_CANDIDATE_POOL,
    RAG_NEWS_TOP_K,
    RSS_FEEDS,
    TELEGRAM_SOURCE_CHANNELS,
    WAZE_BBOX_BOTTOM,
    WAZE_BBOX_LEFT,
    WAZE_BBOX_RIGHT,
    WAZE_BBOX_TOP,
    WAZE_ENV,
    waze_allowed_type_set,
)
from src.core.local_keywords import local_keyword_filter_enabled, matches_local_interest
from src.core.location_extractor import SARAWAK_LOCATION_ALIASES, extract_location_and_state
from src.core.models import NewsArticle, User, UserArticleDelivery
from src.core.rss_limits import effective_rss_limit_per_feed
from src.scrapers.rss_reader import RssItem, fetch_latest_items
from src.scrapers.telegram_reader import (
    canonical_link_for_news_item,
    fetch_latest_telegram_items,
)
from src.scrapers.waze_client import WazeGeoRssError, list_alerts_in_bbox
from src.storage.database import SessionLocal

logger = logging.getLogger(__name__)

_DEDUP_STOPWORDS: Final[set[str]] = {
    "a",
    "an",
    "and",
    "at",
    "breaking",
    "for",
    "from",
    "in",
    "of",
    "on",
    "the",
    "to",
    "update",
    "with",
}


def _tokenize_for_story_dedup(text: str | None) -> set[str]:
    raw = (text or "").strip().lower()
    if not raw:
        return set()
    words = re.findall(r"[a-z0-9]+", raw)
    return {
        w
        for w in words
        if len(w) >= 3 and w not in _DEDUP_STOPWORDS and not w.isdigit()
    }


def _jaccard_similarity(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    union = len(a | b)
    if union == 0:
        return 0.0
    return len(a & b) / union


def _find_cross_source_duplicate_story_match(
    *,
    title_tokens: set[str],
    body_tokens: set[str],
    seen_signatures: list[tuple[set[str], set[str]]],
) -> tuple[int, str, float] | None:
    if not seen_signatures:
        return None

    for idx, (seen_title, seen_body) in enumerate(seen_signatures):
        title_sim = _jaccard_similarity(title_tokens, seen_title)
        if title_sim >= CROSS_SOURCE_DEDUP_TITLE_JACCARD_THRESHOLD:
            return (idx, "title", title_sim)

        # Body comparison is a fallback when title wording differs.
        if (
            len(body_tokens) >= CROSS_SOURCE_DEDUP_MIN_BODY_TOKENS
            and len(seen_body) >= CROSS_SOURCE_DEDUP_MIN_BODY_TOKENS
        ):
            body_sim = _jaccard_similarity(body_tokens, seen_body)
            if body_sim >= CROSS_SOURCE_DEDUP_BODY_JACCARD_THRESHOLD:
                return (idx, "body", body_sim)

    return None


def _cluster_ranked_articles_cross_source(
    ranked_articles: list[NewsArticle], *, max_items: int
) -> list[tuple[NewsArticle, list[NewsArticle]]]:
    if not ranked_articles:
        return []
    if not CROSS_SOURCE_DEDUP_ENABLED:
        return [(art, [art]) for art in ranked_articles[:max_items]]

    clusters: list[tuple[NewsArticle, list[NewsArticle]]] = []
    seen_signatures: list[tuple[set[str], set[str]]] = []
    seen_links: set[str] = set()

    for art in ranked_articles:
        link = canonical_link_for_news_item(
            RssItem(
                title=art.title or "",
                link=art.link or "",
                summary=art.raw_summary or art.ai_summary or "",
                source=art.source or "",
                published=art.created_at,
            )
        )
        dedup_link = link.lower() if link.lower().startswith("https://t.me/") else link
        if dedup_link in seen_links:
            continue

        title_tokens = _tokenize_for_story_dedup(art.title)
        body_tokens = _tokenize_for_story_dedup(
            f"{art.raw_summary or ''} {art.ai_summary or ''}"
        )

        duplicate_match = _find_cross_source_duplicate_story_match(
            title_tokens=title_tokens,
            body_tokens=body_tokens,
            seen_signatures=seen_signatures,
        )
        if duplicate_match is not None:
            cluster_idx, match_by, score = duplicate_match
            clusters[cluster_idx][1].append(art)
            seen_links.add(dedup_link)
            if CROSS_SOURCE_DEDUP_DEBUG:
                logger.info(
                    "[cross-source-dedup] group article_id=%s into_cluster=%s by=%s score=%.3f title=%r",
                    art.id,
                    cluster_idx,
                    match_by,
                    score,
                    (art.title or "")[:160],
                )
            continue

        seen_links.add(dedup_link)
        seen_signatures.append((title_tokens, body_tokens))
        clusters.append((art, [art]))
        if CROSS_SOURCE_DEDUP_DEBUG:
            logger.info(
                "[cross-source-dedup] keep article_id=%s cluster=%s title_tokens=%d body_tokens=%d title=%r",
                art.id,
                len(clusters) - 1,
                len(title_tokens),
                len(body_tokens),
                (art.title or "")[:160],
            )
        if len(clusters) >= max_items:
            break

    return clusters


def _dedup_ranked_articles_cross_source(
    ranked_articles: list[NewsArticle], *, max_items: int
) -> list[NewsArticle]:
    clusters = _cluster_ranked_articles_cross_source(
        ranked_articles, max_items=max_items
    )
    return [primary for primary, _members in clusters]


def _format_sources_html_from_article_cluster(
    cluster: list[NewsArticle], *, escape_html
) -> str:
    rendered: list[str] = []
    seen: set[tuple[str, str]] = set()
    for art in cluster:
        source_name = (art.source or "").strip()
        if source_name.lower().startswith("http"):
            source_name = _get_source_name(source_name)
        if not source_name:
            source_name = "Unknown Source"
        link = (art.link or "").strip()
        key = (source_name, link)
        if key in seen:
            continue
        seen.add(key)
        safe_source = escape_html(source_name)
        if link:
            rendered.append(f'<a href="{link}">{safe_source}</a>')
        else:
            rendered.append(safe_source)
    if not rendered:
        return "Sources: Unknown Source"
    return f"Sources: {', '.join(rendered)}"


def _record_deliveries_for_user(
    session,
    user_id: int,
    articles: List[NewsArticle],
    sent_at: datetime,
) -> None:
    """Insert delivery rows so this user will not see the same articles again while dedup is on."""
    for art in articles:
        already = session.execute(
            select(UserArticleDelivery.id).where(
                UserArticleDelivery.user_id == user_id,
                UserArticleDelivery.article_id == art.id,
            )
        ).scalar_one_or_none()
        if already is None:
            session.add(
                UserArticleDelivery(
                    user_id=user_id,
                    article_id=art.id,
                    sent_at=sent_at,
                )
            )


def _get_or_create_article_for_rss_item(session, item: RssItem) -> NewsArticle:
    link = canonical_link_for_news_item(item)
    if link.lower().startswith("https://t.me/"):
        existing = session.execute(
            select(NewsArticle).where(func.lower(NewsArticle.link) == link.lower())
        ).scalar_one_or_none()
    else:
        existing = session.execute(
            select(NewsArticle).where(NewsArticle.link == link)
        ).scalar_one_or_none()
    if existing:
        return existing

    loc, st = extract_location_and_state(item.title, item.summary)
    row = NewsArticle(
        title=item.title,
        link=link,
        source=_get_source_name(item.source),
        raw_summary=item.summary,
        location=loc,
        state=st,
        category=_extract_category(item.title, item.summary),
    )
    session.add(row)
    session.flush()
    return row


def _deduplicate_items(items: List[RssItem], max_items: int) -> List[RssItem]:
    """
    Deduplicate items by canonical link first, then near-duplicate story text.
    Items should be pre-sorted by date (newest first) to ensure latest news is prioritized.
    """
    seen_links = set()
    seen_story_signatures: list[tuple[set[str], set[str]]] = []
    unique_items: List[RssItem] = []
    for item in items:
        link = canonical_link_for_news_item(item)
        dedup_key = link.lower() if link.lower().startswith("https://t.me/") else link
        if dedup_key in seen_links:
            continue
        title_tokens = _tokenize_for_story_dedup(item.title)
        body_tokens = _tokenize_for_story_dedup(item.summary)
        duplicate_match = None
        if CROSS_SOURCE_DEDUP_ENABLED:
            duplicate_match = _find_cross_source_duplicate_story_match(
                title_tokens=title_tokens,
                body_tokens=body_tokens,
                seen_signatures=seen_story_signatures,
            )
        if duplicate_match is not None:
            if CROSS_SOURCE_DEDUP_DEBUG:
                _cluster_idx, match_by, score = duplicate_match
                logger.info(
                    "[cross-source-dedup] skip rss_item by=%s score=%.3f title=%r",
                    match_by,
                    score,
                    (item.title or "")[:160],
                )
            continue
        seen_links.add(dedup_key)
        seen_story_signatures.append((title_tokens, body_tokens))
        unique_items.append(item)
        if CROSS_SOURCE_DEDUP_DEBUG:
            logger.info(
                "[cross-source-dedup] keep rss_item title_tokens=%d body_tokens=%d title=%r",
                len(title_tokens),
                len(body_tokens),
                (item.title or "")[:160],
            )
        if len(unique_items) >= max_items:
            break
    return unique_items


def _sort_items_by_date(items: List[RssItem]) -> List[RssItem]:
    """
    Sort RSS items by published date, newest first.
    Items without a published date are placed at the end.
    """
    def get_sort_key(item: RssItem) -> tuple:
        # Use a large timestamp for items without dates so they sort to the end
        if item.published:
            # Negate timestamp to sort descending (newest first)
            return (-item.published.timestamp(),)
        else:
            # Items without dates go to the end
            return (float('inf'),)
    
    return sorted(items, key=get_sort_key)


def _fetch_combined_latest_items(
    *,
    limit_per_feed: int,
    max_age_hours: int | None = None,
) -> List[RssItem]:
    kwargs: dict[str, int] = {"limit_per_feed": limit_per_feed}
    if max_age_hours is not None:
        kwargs["max_age_hours"] = max_age_hours

    items: List[RssItem] = fetch_latest_items(RSS_FEEDS, **kwargs)
    telegram_items = fetch_latest_telegram_items(
        TELEGRAM_SOURCE_CHANNELS,
        limit_per_source=limit_per_feed,
        max_age_hours=max_age_hours if max_age_hours is not None else 24,
    )
    if telegram_items:
        items.extend(telegram_items)
    return items


def _matches_location_filter(title: str, summary: str, locations: str) -> bool:
    """
    Check if a news item matches the user's location filter.
    If locations is empty, all Sarawak news matches (default behavior).
    
    Priority detection:
    1. Check if summary starts with city name (e.g., "KOTA SAMARAHAN (March 7):")
    2. Check if title contains city name
    3. Check if summary contains city name
    
    This is more accurate since Sarawak news often starts with the city name.
    """
    if not locations or locations.strip() == "":
        return True  # Empty means show all Sarawak news

    location_list = [loc.strip().lower() for loc in locations.split(",") if loc.strip()]
    if not location_list:
        return True

    # Map common variations and aliases (all lowercase for matching)
    location_mapping = {
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

    # Normalize text for searching
    title_lower = title.lower()
    summary_lower = (summary or "").lower()
    
    # Build all variations to check for each user-selected location
    all_variations_to_check = []
    for user_location in location_list:
        # Add the user's input as-is
        all_variations_to_check.append(user_location)
        
        # Add mapped variations
        for key, variations in location_mapping.items():
            if user_location == key or user_location in variations:
                all_variations_to_check.extend(variations)
    
    # Remove duplicates while preserving order
    seen = set()
    unique_variations = []
    for var in all_variations_to_check:
        if var not in seen:
            seen.add(var)
            unique_variations.append(var)
    
    # Priority 1: Check if summary STARTS with city name (most reliable)
    # Format: "KOTA SAMARAHAN (March 7):" or "KUCHING:" etc.
    if summary_lower:
        # Check first 100 characters (enough to catch "CITY NAME (DATE):")
        summary_start = summary_lower[:100].strip()
        for variation in unique_variations:
            # Check if summary starts with the city name (with optional parentheses/date after)
            if summary_start.startswith(variation):
                return True
            # Also check uppercase version (e.g., "KOTA SAMARAHAN")
            if summary_start.startswith(variation.upper()):
                return True
    
    # Priority 2: Check if title contains city name
    for variation in unique_variations:
        if variation in title_lower:
            return True
    
    # Priority 3: Check if summary contains city name (anywhere)
    if summary_lower:
        for variation in unique_variations:
            if variation in summary_lower:
                return True

    return False


def _is_locations_filter_all_sarawak(locations_filter: str | None) -> bool:
    """
    True when the user did not narrow to specific cities (all Sarawak-wide news allowed).
    Supabase UI may show blank as EMPTY; some rows use the literal 'sarawak' for whole-region.
    """
    if locations_filter is None:
        return True
    s = locations_filter.strip().lower()
    if not s or s in {"empty", "sarawak", "all", "all sarawak"}:
        return True
    return False


def _row_matches_user_locations(
    *,
    title: str,
    summary: str | None,
    state: str | None,
    location: str | None,
    locations_filter: str,
) -> bool:
    """
    Hard filter: if the user selected specific location(s), keep only matching articles.
    Uses DB location column when set; otherwise headline/summary heuristics (_matches_location_filter).
    """
    if _is_locations_filter_all_sarawak(locations_filter):
        return True

    user_locs = {loc.strip().lower() for loc in locations_filter.split(",") if loc.strip()}
    if not user_locs:
        return True

    loc_col = (location or "").strip().lower()
    if loc_col:
        return loc_col in user_locs

    return _matches_location_filter(title, summary or "", locations_filter)


def post_matches_user_locations_filter(title: str, body: str | None, locations_filter: str) -> bool:
    """Channel posts / urgent alerts: same rules as DB rows using extracted location/state."""
    loc, st = extract_location_and_state(title, body)
    return _row_matches_user_locations(
        title=title,
        summary=body,
        state=st,
        location=loc,
        locations_filter=locations_filter,
    )


def _article_source_is_telegram(source: str | None) -> bool:
    """NewsArticle.source is a display name like 'Telegram (@swbnews)'."""
    return (source or "").lower().startswith("telegram")


def _db_article_eligible_for_user_pref(
    art: NewsArticle,
    *,
    categories_filter: str,
    area_keywords_filter: str,
    locations_filter: str = "",
) -> bool:
    """
    Per-user gate for DB-backed news rows before geo ranking.
    Telegram posts: if the user set Area Keywords, require a substring match; otherwise use the
    global Sarawak_Local_Keywords file. Non-Telegram: unchanged (global keywords + categories).
    """
    if not _matches_user_category_filter(
        stored_category=getattr(art, "category", None),
        state=getattr(art, "state", None),
        title=art.title or "",
        summary=art.raw_summary or art.ai_summary or "",
        categories_filter=categories_filter,
    ):
        return False
    combined = f"{art.title}\n{art.ai_summary or art.raw_summary or ''}"
    if _article_source_is_telegram(art.source):
        if (area_keywords_filter or "").strip():
            if not _matches_area_keywords_filter(combined, area_keywords_filter):
                return False
        elif not matches_local_interest(art.title, art.raw_summary):
            return False
    elif not matches_local_interest(art.title, art.raw_summary):
        return False

    if not _row_matches_user_locations(
        title=art.title or "",
        summary=art.raw_summary or art.ai_summary,
        state=getattr(art, "state", None),
        location=getattr(art, "location", None),
        locations_filter=locations_filter,
    ):
        return False
    return True


def _rss_item_prefilter_for_user_pref(item: RssItem, *, area_keywords_filter: str) -> bool:
    """Live-fetch fallback: same Telegram vs global rule as _db_article_eligible_for_user_pref (no category here)."""
    is_tg = (item.source or "").lower().startswith("telegram:")
    if is_tg:
        if (area_keywords_filter or "").strip():
            combined = f"{item.title}\n{item.summary or ''}"
            return _matches_area_keywords_filter(combined, area_keywords_filter)
        return matches_local_interest(item.title, item.summary)
    return matches_local_interest(item.title, item.summary)


def _matches_area_keywords_filter(text: str, area_keywords: str) -> bool:
    """
    Substring match for roads/areas (roads, taman, kampung).

    - News: combined with _geo_priority_rank so matches get higher priority (not exclusion).

    If area_keywords is empty: match everything (for the boolean check in isolation).
    Otherwise: true if ANY keyword appears in text (case-insensitive).
    """
    if not area_keywords or area_keywords.strip() == "":
        return True

    keywords = [k.strip().lower() for k in area_keywords.split(",") if k.strip()]
    if not keywords:
        return True

    haystack = (text or "").lower()
    return any(k in haystack for k in keywords)


def _waze_alert_text_for_area_match(alert: dict[str, Any]) -> str:
    """Concatenate Waze fields so area-keyword rules match roads/neighbourhoods."""
    parts = [
        str(alert.get("street") or ""),
        str(alert.get("city") or ""),
        str(alert.get("reportDescription") or ""),
        str(alert.get("subtype") or ""),
        str(alert.get("type") or ""),
    ]
    return " ".join(parts)


def build_waze_section_for_area_keywords(
    area_keywords: str,
    *,
    max_show: int = 6,
    fetch_pool: int = 150,
) -> str | None:
    """
    Developer-only Waze preview: live-map alerts in the env bbox, filtered by the
    same Area Keywords substring rules as news ranking. Not appended to user /latest
    or scheduled pushes — use /devwaze (see handlers). Returns None if keywords empty.
    """
    if not (area_keywords or "").strip():
        return None

    if max_show < 1:
        max_show = 1
    if fetch_pool < max_show:
        fetch_pool = max_show

    def escape_html(text: str) -> str:
        return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    kw = area_keywords.strip()
    try:
        pool = list_alerts_in_bbox(
            top=WAZE_BBOX_TOP,
            bottom=WAZE_BBOX_BOTTOM,
            left=WAZE_BBOX_LEFT,
            right=WAZE_BBOX_RIGHT,
            env=WAZE_ENV,
            allowed_types=waze_allowed_type_set(),
            max_alerts=fetch_pool,
        )
    except WazeGeoRssError as e:
        return (
            "<b>Road alerts (Waze)</b>\n"
            f"{escape_html(str(e))}\n"
            "<i>Second source: alerts matching your Area Keywords. If you see 403, set WAZE_COOKIE.</i>"
        )

    filtered = [
        a
        for a in pool
        if _matches_area_keywords_filter(_waze_alert_text_for_area_match(a), kw)
    ]
    filtered = filtered[:max_show]

    if not filtered:
        return (
            "<b>Road alerts (Waze)</b>\n"
            "<i>No Waze reports match your area keywords right now.</i>"
        )

    sentences = waze_alerts_to_news_sentences(filtered)
    lines: List[str] = [
        "<b>Road alerts (Waze)</b>",
        "<i>Second source (crowd map): reports matching your Area Keywords — live JSON + Ollama</i>",
        "",
    ]
    waze_map_url = "https://www.waze.com/live-map"
    for alert, sentence in zip(filtered, sentences):
        lat, lon = alert.get("latitude"), alert.get("longitude")
        if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
            loc_url = f"https://www.waze.com/live-map?latlng={lat}%2C{lon}"
        else:
            loc_url = waze_map_url
        lines.append(f"• {escape_html(sentence)}")
        lines.append(f'  <a href="{loc_url}">Open in Waze map</a>')
        lines.append("")

    lines.append(f'<a href="{waze_map_url}">Waze Live Map</a>')
    return "\n".join(lines).strip()


def _fallback_summary_from_text(text: str, max_words: int = 50) -> str:
    """
    Deterministic fallback summary when LLM output is unavailable/unreliable.
    """
    cleaned = strip_markdown_artifacts_for_plain_text(" ".join((text or "").split()))
    if not cleaned:
        return "(No summary available right now.)"
    return clip_plain_text_to_word_limit(cleaned, max_words)


def backfill_ai_summaries_for_article_ids(article_ids: List[int]) -> int:
    """
    Fill ai_summary for the given news_articles rows (same logic as get_latest_news_text_for_user:
    optional full-page scrape, Ollama summarize, deterministic fallback). Commits per row so one
    failure does not block the rest.
    """
    if not article_ids:
        return 0
    from src.ai.summarizer import summarize
    from src.scrapers.article_scraper import extract_article_content

    updated = 0
    with SessionLocal() as session:
        for aid in article_ids:
            art = session.get(NewsArticle, aid)
            if art is None:
                continue
            if (art.ai_summary or "").strip():
                continue
            try:
                article_text = extract_article_content(art.link)
                source_text = article_text or art.raw_summary or art.title or ""
                ai = summarize(source_text, title=art.title or "")
                if not ai:
                    ai = _fallback_summary_from_text(art.raw_summary or art.title or "", max_words=80)
                art.ai_summary = normalize_stored_ai_summary(ai)
                session.commit()
                updated += 1
            except Exception:
                session.rollback()
                try:
                    art2 = session.get(NewsArticle, aid)
                    if art2 is not None and not (art2.ai_summary or "").strip():
                        art2.ai_summary = _fallback_summary_from_text(
                            art2.raw_summary or art2.title or "", max_words=80
                        )
                        session.commit()
                        updated += 1
                except Exception:
                    session.rollback()
    return updated


def build_urgent_preview(title: str, summary: str | None, max_words: int = 45) -> str:
    """
    Build a non-empty, concise preview line for urgent alert messages.
    Prefers summary body; falls back to title when summary is missing.
    """
    def _normalize_for_title_match(s: str) -> str:
        # Keep it intentionally simple: whitespace + lowercase + trim common punctuation.
        norm = " ".join((s or "").split()).strip().lower()
        # Trim leading/trailing punctuation so minor formatting differences still match.
        return norm.strip("\"'()[]{}<>.,;:!?")

    body = (summary or "").strip()
    if body and (title or "").strip():
        # Avoid repeating the title line when channel posts start with the headline.
        first_line = body.splitlines()[0].strip() if body.splitlines() else ""
        if first_line:
            norm_title = _normalize_for_title_match(title)
            norm_first = _normalize_for_title_match(first_line)

            if norm_first and norm_title:
                # Exact match first, then a safe "contains" match (only when title is long enough).
                contains_ok = len(norm_title) >= 8 and (
                    norm_first == norm_title or norm_first in norm_title or norm_title in norm_first
                )
                if norm_first == norm_title or contains_ok:
                    body = "\n".join(body.splitlines()[1:]).strip()

    source_text = body or title or ""
    return _fallback_summary_from_text(source_text, max_words=max_words)


def _geo_priority_rank(
    *,
    title: str,
    summary: str,
    locations_filter: str,
    area_keywords_filter: str,
    state: str | None = None,
    location: str | None = None,
) -> int:
    """
    Geographic relevance rank (lower is higher priority). Used for ordering only:
    articles that do not match area keywords are still eligible; matches rank higher.

    0: matches area keywords (very specific)
    1: matches selected location(s) (cities)
    2: mentions Sarawak
    3: mentions Malaysia
    4: world/other
    """
    def _is_sarawak_related_text(text: str) -> bool:
        t = (text or "").lower()
        sarawak_terms = [
            "sarawak",
            # Major cities / regions (covers most Sarawak headlines)
            "kuching",
            "miri",
            "sibu",
            "bintulu",
            "serian",
            "sarikei",
            "sri aman",
            "sriaman",
            "kota samarahan",
            "samarahan",
            # Other towns/areas from your location mapping
            "mukah",
            "limbang",
            "lawas",
            "betong",
            "saratok",
            "kapit",
            "marudi",
            "belaga",
        ]
        return any(term in t for term in sarawak_terms)

    combined = f"{title}\n{summary}".lower()

    if area_keywords_filter.strip() and _matches_area_keywords_filter(combined, area_keywords_filter):
        return 0

    # If we have extracted location/state, prefer it over heuristic matching.
    if locations_filter.strip():
        user_locations = {
            loc.strip().lower() for loc in (locations_filter or "").split(",") if loc.strip()
        }

        if (state or "").lower() == "sarawak":
            if location and location.strip().lower() in user_locations:
                return 1
            # Sarawak article, but not in the exact user-selected location.
            return 2

        # Fallback to old heuristic matching when state/location are missing.
        if _matches_location_filter(title, summary, locations_filter):
            return 1

    if (state or "").lower() == "sarawak":
        return 2

    if (state or "").lower() == "other":
        return 3

    if _is_sarawak_related_text(combined):
        return 2

    if "malaysia" in combined:
        return 3

    return 4


def _should_apply_area_priority(
    *,
    records: list[tuple[str, str]],
    area_keywords_filter: str,
) -> bool:
    """
    Apply area-keyword priority only when at least one candidate matches.
    If no candidate matches, caller should fall back to broader geo priority.
    """
    if not area_keywords_filter.strip():
        return False

    for title, summary in records:
        combined = f"{title}\n{summary}"
        if _matches_area_keywords_filter(combined, area_keywords_filter):
            return True
    return False


def _extract_category(title: str, summary: str | None) -> str:
    """
    Extract a category tag from the news title/summary.
    Returns a category like "Local", "Sports", "Politics", "Business", etc.
    Note: "Local" is checked last since most Sarawak news contains location names.
    """
    text = (title + " " + (summary or "")).lower()

    # Check more specific categories first (before "Local" which matches too broadly)
    
    # Emergency/Disaster - highest priority for urgent news
    if any(word in text for word in ["flood", "accident", "fire", "emergency", "disaster", "crash", "collision", "explosion", "evacuation"]):
        return "Emergency"
    
    # Health - medical, public health, hospitals
    if any(word in text for word in ["health", "hospital", "medical", "covid", "disease", "clinic", "doctor", "patient", "treatment", "vaccine", "rabies"]):
        return "Health"
    
    # Sports - various sports keywords
    if any(word in text for word in ["sport", "football", "soccer", "badminton", "tennis", "olympic", "athlete", "championship", "tournament", "match", "game", "team"]):
        return "Sports"
    
    # Politics - government, elections, ministers
    if any(word in text for word in ["politic", "minister", "government", "parliament", "election", "minister", "chief minister", "assembly", "cabinet", "policy", "bill", "law"]):
        return "Politics"
    
    # Education - schools, universities, students
    if any(word in text for word in ["education", "school", "university", "student", "teacher", "college", "campus", "exam", "graduation", "scholarship"]):
        return "Education"
    
    # Business/Economy - trade, markets, companies
    if any(word in text for word in ["business", "economy", "trade", "market", "stock", "company", "investment", "bank", "financial", "revenue", "profit", "commercial"]):
        return "Business"
    
    # Technology - tech news, digital, IT
    if any(word in text for word in ["technology", "tech", "digital", "internet", "software", "app", "online", "cyber", "computer", "ai", "artificial intelligence"]):
        return "Technology"
    
    # Entertainment/Culture - festivals, events, arts, culture
    if any(word in text for word in ["festival", "event", "concert", "art", "culture", "entertainment", "music", "dance", "performance", "exhibition", "celebration", "creative"]):
        return "Entertainment"
    
    # Environment - nature, climate, conservation
    if any(word in text for word in ["environment", "climate", "nature", "conservation", "forest", "wildlife", "pollution", "recycling", "green", "sustainable"]):
        return "Environment"
    
    # Infrastructure - roads, buildings, construction, utilities
    if any(word in text for word in ["infrastructure", "road", "bridge", "construction", "building", "project", "development", "utility", "water", "electricity", "power"]):
        return "Infrastructure"
    
    # Crime/Safety - crime, police, security
    if any(word in text for word in ["crime", "police", "arrest", "theft", "robbery", "murder", "suspect", "investigation", "court", "trial", "safety", "security"]):
        return "Crime"
    
    # Social/Community - community events, social issues
    if any(word in text for word in ["community", "social", "welfare", "charity", "donation", "volunteer", "ngo", "organization", "society"]):
        return "Social"
    
    # Tourism - travel, tourism, hotels
    if any(word in text for word in ["tourism", "tourist", "travel", "hotel", "resort", "visitor", "attraction", "destination"]):
        return "Tourism"
    
    # Food - restaurants, food, dining
    if any(word in text for word in ["food", "restaurant", "cuisine", "dining", "cafe", "culinary", "gastronomy", "recipe"]):
        return "Food"

    # Agriculture / fisheries / rural economy
    if any(
        word in text
        for word in [
            "agriculture",
            "plantation",
            "palm oil",
            "farmer",
            "farming",
            "fishery",
            "fishing",
            "padi",
            "livestock",
            "veterinar",
            "crop",
            "harvest",
            "fama",
        ]
    ):
        return "Agriculture"

    # Transport (operations, delays, airports) — not bridge inaugurations (Infrastructure)
    if any(
        word in text
        for word in [
            "airport",
            "flight",
            "airline",
            "maswings",
            "ferry",
            "bus terminal",
            "traffic jam",
            "road closure",
            "closed to traffic",
            "mikro",
            "express boat",
        ]
    ):
        return "Transport"

    # Weather / Met — forecasts and alerts (floods as disaster stay Emergency if keyword hit earlier)
    if any(
        word in text
        for word in [
            "metmalaysia",
            "met malaysia",
            "meteorolog",
            "weather forecast",
            "monsoon",
            "thunderstorm",
            "heatwave",
            "cuaca",
        ]
    ):
        return "Weather"

    # Culture / heritage / arts (not generic "event")
    if any(
        word in text
        for word in [
            "heritage",
            "museum",
            "cultural heritage",
            "traditional dance",
            "gawai",
            "kaamatan",
            "cultural village",
            "handicraft",
        ]
    ):
        return "Culture"

    # Religion — places of worship and religious observance
    if any(
        word in text
        for word in [
            "surau",
            "mosque",
            "masjid",
            "church",
            "temple",
            "easter",
            "ramadan",
            "hari raya",
            "christmas",
            "good friday",
            "interfaith",
        ]
    ):
        return "Religion"

    # Consumer / prices / shortages (retail staples)
    if any(
        word in text
        for word in [
            "price of",
            "shortage of",
            "cooking oil",
            "sugar supply",
            "subsid",
            "harga",
            "inflation",
            "consumer",
        ]
    ):
        return "Consumer"

    # Housing / property schemes
    if any(
        word in text
        for word in [
            "housing scheme",
            "affordable housing",
            "rumah",
            "stamp duty",
            "squatter",
            "pr1ma",
            "public housing",
        ]
    ):
        return "Housing"

    # Defence / security forces (not civilian police Crime)
    if any(
        word in text
        for word in [
            "malaysian armed forces",
            "malaysian army",
            "royal malaysian navy",
            "tentera",
            "esscom",
            "military exercise",
            "border security",
        ]
    ):
        return "Defence"
    
    # Default to "Local" for general Sarawak news (checked last since location names appear in most news)
    # Only use "Local" if no other category matched
    if any(word in text for word in ["sarawak", "kuching", "miri", "sibu", "borneo", "bintulu", "samarahan"]):
        return "Local"
    
    # Fallback to "General" if nothing matches
    return "General"


def _matches_user_category_filter(
    *,
    stored_category: str | None,
    state: str | None,
    title: str,
    summary: str | None,
    categories_filter: str,
) -> bool:
    """
    Match user /settings categories against a resolved taxonomy label (stored on the article
    or derived with the same rules as _extract_category). Replaces substring search on title/body.

    Special case: token ``sarawak`` matches Sarawak-tagged geography (DB ``state``), the
    ``Local`` label, or the word \"sarawak\" in title/summary (RSS rows often lack ``state``).
    """
    if not categories_filter or not categories_filter.strip():
        return True

    tokens = [t.strip().lower() for t in categories_filter.split(",") if t.strip()]
    if not tokens:
        return True

    resolved = (stored_category or "").strip()
    if not resolved:
        resolved = _extract_category(title, summary or "")

    ac_lower = resolved.lower()
    text_blob = (title + " " + (summary or "")).lower()
    st = (state or "").strip().lower()

    for tok in tokens:
        if tok == "sarawak":
            if st == "sarawak" or ac_lower == "local" or "sarawak" in text_blob:
                return True
            continue
        if ac_lower == tok:
            return True

    return False


def _get_category_with_llm_fallback(title: str, summary: str | None) -> str:
    """
    Prefer LLM-based classification (more accurate), fallback to keyword rules.
    Skips Ollama when the item fails the local keyword pre-filter (if enabled).
    """
    if not matches_local_interest(title, summary):
        return _extract_category(title, summary)
    llm_text = (title + "\n" + (summary or "")).strip()
    llm_category = classify_category(llm_text)
    if llm_category:
        return llm_category
    return _extract_category(title, summary)


def category_label_for_article(art: NewsArticle) -> str:
    """Prefer persisted category; otherwise same pipeline as before (LLM + keyword fallback)."""
    if (getattr(art, "category", None) or "").strip():
        return (art.category or "").strip()
    return _get_category_with_llm_fallback(art.title, art.ai_summary or art.raw_summary)


def _get_source_name(source_url: str) -> str:
    """
    Extract a friendly source name from the RSS feed URL.
    """
    if source_url.lower().startswith("telegram:"):
        handle = source_url.split(":", 1)[1].strip()
        if handle.replace("-", "").isdigit():
            return "Telegram"
        return f"Telegram (@{handle.lstrip('@')})"

    source_mapping = {
        "sarawaktribune.com": "Sarawak Tribune",
        "seehua.com": "See Hua Daily News",
        "theborneopost.com": "Borneo Post Online",
        "cms.buletintv3.my": "Buletin TV3",
        "buletintv3.my": "Buletin TV3",
        "berita.rtm.gov.my": "Berita RTM",
    }

    for domain, name in source_mapping.items():
        if domain in source_url.lower():
            return name

    raw = (source_url or "").strip()
    if not raw:
        return "Unknown Source"
    if not (raw.startswith("http://") or raw.startswith("https://")):
        return raw

    # Fallback: extract domain name
    try:
        from urllib.parse import urlparse
        parsed = urlparse(raw)
        domain = parsed.netloc.replace("www.", "")
        return domain.split(".")[0].title() if domain else "Unknown Source"
    except Exception:
        return "Unknown Source"


def _is_urgent_utility_alert(title: str, summary: str | None) -> bool:
    """
    Detect urgent utility/service disruption style alerts.
    Prioritizes water/electric disruptions and emergency interruption notices.
    """
    text = f"{title}\n{summary or ''}".lower()

    utility_terms = [
        "water",
        "water supply",
        "water disruption",
        "water supply interruption",
        "pipe burst",
        "kwb",
        "jbalb",
        "laku",
        "electric",
        "electricity",
        "power",
        "blackout",
        "power outage",
        "seb",
        "sarawak energy",
    ]
    disruption_terms = [
        "disruption",
        "interruption",
        "outage",
        "shutdown",
        "breakdown",
        "cut off",
        "cut-off",
        "scheduled maintenance",
        "urgent repair",
        "repair works",
        "emergency",
        "alert",
        "notice",
        "advisory",
    ]

    has_utility = any(term in text for term in utility_terms)
    has_disruption = any(term in text for term in disruption_terms)
    return has_utility and has_disruption


def get_recent_urgent_alert_items(
    *,
    within_minutes: int = 30,
    max_items: int = 5,
) -> list[dict[str, str]]:
    """
    Return recent urgent utility alert candidates from DB.
    """
    cutoff = datetime.utcnow() - timedelta(minutes=within_minutes)
    with SessionLocal() as session:
        rows: List[NewsArticle] = list(
            session.execute(
                select(NewsArticle)
                .where(NewsArticle.created_at >= cutoff)
                .order_by(NewsArticle.created_at.desc())
            )
            .scalars()
            .all()
        )

    results: list[dict[str, str]] = []
    for art in rows:
        if not _is_urgent_utility_alert(art.title, art.raw_summary):
            continue

        source_name = art.source
        if source_name.lower().startswith("http"):
            source_name = _get_source_name(source_name)

        results.append(
            {
                "title": art.title,
                "link": art.link,
                "summary": build_urgent_preview(art.title, art.raw_summary, max_words=45),
                "source": source_name,
            }
        )
        if len(results) >= max_items:
            break

    return results


def _latest_news_heading_lines(max_items: int) -> List[str]:
    """
    Multi-item /latest keeps a section title; single-item scheduled/test push does not.
    """
    if max_items <= 1:
        return []
    return ["<b>Latest local news with AI summaries:</b>"]


def get_latest_news_text(max_items: int = 3) -> str:
    """
    Fetch latest items from configured RSS feeds and format them
    into HTML suitable for Telegram, including AI summaries.

    No Telegram user context: does not apply per-user delivery dedup.
    For production paths use get_latest_news_text_for_user.
    """
    eff_limit = effective_rss_limit_per_feed(3)
    items: List[RssItem] = _fetch_combined_latest_items(limit_per_feed=eff_limit)

    sorted_items = _sort_items_by_date(items)
    if not sorted_items:
        return (
            "I couldn't fetch any news items right now.\n"
            "<i>This might be a temporary network issue or the sources are unavailable.</i>"
        )

    keyworded = [i for i in sorted_items if matches_local_interest(i.title, i.summary)]
    if not keyworded:
        if local_keyword_filter_enabled():
            return (
                "No recent items matched your local keyword list "
                "(<code>Sarawak_Local_Keywords.txt</code>).\n"
                "<i>Broaden keywords or clear the file to disable this filter.</i>"
            )
        keyworded = sorted_items

    unique_items = _deduplicate_items(keyworded, max_items=max_items)

    if not unique_items:
        return (
            "No new headlines since your last request.\n"
            "<i>You are up to date with the latest news from these sources.</i>"
        )

    to_display: List[RssItem] = unique_items[:max_items]

    if not to_display:
        return (
            "No new headlines since your last request.\n"
            "<i>You are up to date with the latest news from these sources.</i>"
        )

    lines: List[str] = list(_latest_news_heading_lines(max_items))

    for item in to_display:
        # Extract category (LLM first, fallback to keyword rules)
        category = _get_category_with_llm_fallback(item.title, item.summary)

        # Get AI summary - try to fetch full article content first
        from src.scrapers.article_scraper import extract_article_content

        article_text = extract_article_content(item.link)
        if article_text:
            # Use full article content for better summary
            source_text = article_text
        else:
            # Fallback to RSS summary or title if article scraping fails
            source_text = item.summary or item.title

        ai_summary = (
            summarize(source_text, title=item.title)
            if matches_local_interest(item.title, item.summary)
            else None
        )
        if not ai_summary:
            ai_summary = _fallback_summary_from_text(item.summary or item.title, max_words=80)

        # Get source name
        source_name = _get_source_name(item.source)

        # Escape HTML special characters in title and summary
        def escape_html(text: str) -> str:
            return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        escaped_title = escape_html(item.title)
        escaped_summary = escape_html(
            normalize_stored_ai_summary(ai_summary or "")
        )
        escaped_source = escape_html(source_name)

        # Format according to specification:
        # <blockquote>[Category] <b>Title</b></blockquote> (headline in blockquote for visual distinction)
        # Summary (plain text)
        # <a href="link">Source name</a>
        escaped_category = escape_html(category)
        lines.append(f"<blockquote>[{escaped_category}] <b>{escaped_title}</b></blockquote>")
        lines.append(escaped_summary)
        lines.append("")
        lines.append(f'Sources: <a href="{item.link}">{escaped_source}</a>')
        lines.append("────────────")  # Visual separator between items

    # Remove trailing separator before final join
    if lines and lines[-1] == "────────────":
        lines.pop()

    return "\n".join(lines)


# Substring used by the scheduled job to skip sending without advancing last_scheduled_push_at.
SCHEDULED_PUSH_SUMMARY_PENDING_SKIP_MARKER: Final[str] = "ai summaries are still being prepared"

SCHEDULED_PUSH_SUMMARY_PENDING_USER_MESSAGE: Final[str] = (
    "AI summaries are still being prepared for the latest articles.\n"
    "<i>We will include them in your next notification when ready. "
    "Scheduled pushes do not send raw article text.</i>"
)


def get_latest_news_text_for_user(
    telegram_id: int,
    max_items: int = 3,
    *,
    scheduled_push: bool = False,
) -> str:
    """
    Fetch latest items from configured RSS feeds, filtered by user preferences,
    and format them into HTML suitable for Telegram.

    Area Keywords boost *priority* for news whose text mentions those roads/areas
    (other articles still appear). Waze is not included here — use /devwaze (developer).

    When max_items is 1 (scheduled push, /testpush), the outer heading
    "Latest local news with AI summaries:" is omitted for a tighter message.

    With deduplication on, \"already shown\" is tracked per user via UserArticleDelivery,
    not global NewsArticle.last_sent_at.

    When ``scheduled_push`` is True, only rows with a non-empty ``ai_summary`` are eligible.
    If articles match filters but summaries are not ready yet, returns
    :data:`SCHEDULED_PUSH_SUMMARY_PENDING_USER_MESSAGE`. Live RSS fallback is not used.
    """
    from src.core.user_service import get_or_create_user, get_user_preference

    get_or_create_user(telegram_id, username=None)
    preference = get_user_preference(telegram_id)
    categories_filter = preference.categories if preference else ""
    locations_filter = preference.locations if preference else ""
    area_keywords_filter = preference.area_keywords if preference else ""

    def escape_html(text: str) -> str:
        return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    # DB-first: use prefetched articles (fast + cached) and only call LLM when needed.
    now = datetime.utcnow()
    cutoff = now - timedelta(hours=24)

    with SessionLocal() as session:
        user_row = session.execute(
            select(User).where(User.telegram_id == telegram_id)
        ).scalar_one_or_none()
        user_id: int | None = user_row.id if user_row else None

        stmt = (
            select(NewsArticle)
            .where(NewsArticle.created_at >= cutoff)
            .order_by(NewsArticle.created_at.desc())
        )
        if DEDUPLICATION_ENABLED and user_id is not None:
            already_delivered = exists(
                select(UserArticleDelivery.id).where(
                    UserArticleDelivery.article_id == NewsArticle.id,
                    UserArticleDelivery.user_id == user_id,
                )
            )
            stmt = stmt.where(~already_delivered)

        candidates: List[NewsArticle] = list(session.execute(stmt).scalars().all())
        use_area_priority = _should_apply_area_priority(
            records=[(art.title or "", art.ai_summary or art.raw_summary or "") for art in candidates],
            area_keywords_filter=area_keywords_filter,
        )
        effective_area_keywords_filter = area_keywords_filter if use_area_priority else ""

        # Rank by geographic priority (area keywords > location > Sarawak > Malaysia > world)
        ranked: List[tuple[int, datetime, NewsArticle]] = []
        for art in candidates:
            summary_for_rank = art.ai_summary or art.raw_summary or ""
            if not _db_article_eligible_for_user_pref(
                art,
                categories_filter=categories_filter,
                area_keywords_filter=area_keywords_filter,
                locations_filter=locations_filter,
            ):
                continue
            rank = _geo_priority_rank(
                title=art.title,
                summary=summary_for_rank,
                locations_filter=locations_filter,
                area_keywords_filter=effective_area_keywords_filter,
                state=getattr(art, "state", None),
                location=getattr(art, "location", None),
            )
            ranked.append((rank, art.created_at, art))

        # Sort by rank (best first), then newest first
        ranked.sort(key=lambda t: (t[0], -t[1].timestamp()))

        ranked_pick = ranked
        if scheduled_push:
            ranked_pick = [t for t in ranked if (t[2].ai_summary or "").strip()]

        ranked_articles: List[NewsArticle] = [t[2] for t in ranked_pick]
        chosen_clusters = _cluster_ranked_articles_cross_source(
            ranked_articles, max_items=max_items
        )
        chosen: List[NewsArticle] = [primary for primary, _members in chosen_clusters]

        if not chosen:
            if scheduled_push:
                if ranked:
                    return SCHEDULED_PUSH_SUMMARY_PENDING_USER_MESSAGE
                return (
                    "No new headlines since your last request.\n"
                    "<i>You are up to date with the latest news from these sources.</i>"
                )
            # Fallback: live RSS fetch if DB has nothing yet
            eff_rss = effective_rss_limit_per_feed(15)
            items: List[RssItem] = _fetch_combined_latest_items(
                limit_per_feed=eff_rss, max_age_hours=24
            )
            sorted_items = _sort_items_by_date(items)
            if not sorted_items:
                return (
                    "I couldn't fetch any news items right now.\n"
                    "<i>This might be a temporary network issue or the sources are unavailable.</i>"
                )

            kw_sorted = [
                i
                for i in sorted_items
                if _rss_item_prefilter_for_user_pref(i, area_keywords_filter=area_keywords_filter)
            ]
            if not kw_sorted:
                if local_keyword_filter_enabled():
                    return (
                        "No recent items matched your local keyword list "
                        "(<code>Sarawak_Local_Keywords.txt</code>).\n"
                        "<i>Broaden keywords or clear the file to disable this filter.</i>"
                    )
                kw_sorted = sorted_items

            unique_items = _deduplicate_items(kw_sorted, max_items=max_items * 3)

            if not unique_items:
                return (
                    "I couldn't fetch any news items right now.\n"
                    "<i>This might be a temporary network issue or the sources are unavailable.</i>"
                )

            ranked_items: List[tuple[int, float, RssItem]] = []
            use_area_priority_rss = _should_apply_area_priority(
                records=[(item.title or "", item.summary or "") for item in unique_items],
                area_keywords_filter=area_keywords_filter,
            )
            effective_area_keywords_filter_rss = (
                area_keywords_filter if use_area_priority_rss else ""
            )
            for item in unique_items:
                if not _matches_user_category_filter(
                    stored_category=None,
                    state=None,
                    title=item.title or "",
                    summary=item.summary or "",
                    categories_filter=categories_filter,
                ):
                    continue
                loc_rss, st_rss = extract_location_and_state(
                    item.title or "", item.summary or ""
                )
                if not _row_matches_user_locations(
                    title=item.title or "",
                    summary=item.summary,
                    state=st_rss,
                    location=loc_rss,
                    locations_filter=locations_filter,
                ):
                    continue
                rank = _geo_priority_rank(
                    title=item.title,
                    summary=item.summary or "",
                    locations_filter=locations_filter,
                    area_keywords_filter=effective_area_keywords_filter_rss,
                    state=st_rss,
                    location=loc_rss,
                )
                published_ts = item.published.timestamp() if item.published else 0.0
                ranked_items.append((rank, published_ts, item))

            ranked_items.sort(key=lambda t: (t[0], -t[1]))
            ranked_rss_items: List[RssItem] = [t[2] for t in ranked_items]

            if not ranked_rss_items:
                return (
                    "No news items match your current filters (categories/locations).\n"
                    "<i>Try adjusting your settings with /settings to see more news.</i>"
                )

            to_display: List[RssItem] = []
            displayed_articles: List[NewsArticle] = []

            if not DEDUPLICATION_ENABLED or user_id is None:
                for item in ranked_rss_items[:max_items]:
                    orm_art = _get_or_create_article_for_rss_item(session, item)
                    to_display.append(item)
                    displayed_articles.append(orm_art)
            else:
                for item in ranked_rss_items:
                    orm_art = _get_or_create_article_for_rss_item(session, item)
                    dup = session.execute(
                        select(UserArticleDelivery.id).where(
                            UserArticleDelivery.user_id == user_id,
                            UserArticleDelivery.article_id == orm_art.id,
                        )
                    ).scalar_one_or_none()
                    if dup is not None:
                        continue
                    to_display.append(item)
                    displayed_articles.append(orm_art)
                    if len(to_display) >= max_items:
                        break

            if DEDUPLICATION_ENABLED and user_id is not None and displayed_articles:
                _record_deliveries_for_user(session, user_id, displayed_articles, now)
            session.commit()

            if not to_display:
                return (
                    "No new headlines since your last request.\n"
                    "<i>You are up to date with the latest news from these sources.</i>"
                )

            from src.scrapers.article_scraper import extract_article_content

            lines: List[str] = list(_latest_news_heading_lines(max_items))
            for item in to_display:
                category = _get_category_with_llm_fallback(item.title, item.summary)
                article_text = extract_article_content(item.link)
                source_text = article_text or item.summary or item.title
                ai_summary = (
                    summarize(source_text, title=item.title)
                    if matches_local_interest(item.title, item.summary)
                    else None
                )
                if not ai_summary:
                    ai_summary = _fallback_summary_from_text(
                        item.summary or item.title, max_words=80
                    )
                source_name = _get_source_name(item.source)

                escaped_title = escape_html(item.title)
                escaped_summary = escape_html(
                    normalize_stored_ai_summary(ai_summary or "")
                )
                escaped_source = escape_html(source_name)
                escaped_category = escape_html(category)

                lines.append(f"<blockquote>[{escaped_category}] <b>{escaped_title}</b></blockquote>")
                lines.append(escaped_summary)
                lines.append("")
                lines.append(f'Sources: <a href="{item.link}">{escaped_source}</a>')
                lines.append("────────────")

            if lines and lines[-1] == "────────────":
                lines.pop()
            return "\n".join(lines)

        # Ensure ai_summary exists (cache); scheduled pushes only use rows already summarized.
        from src.scrapers.article_scraper import extract_article_content

        if not scheduled_push:
            for art in chosen:
                if art.ai_summary:
                    continue
                article_text = extract_article_content(art.link)
                source_text = article_text or art.raw_summary or art.title
                art.ai_summary = summarize(source_text, title=art.title)
                if not art.ai_summary:
                    art.ai_summary = _fallback_summary_from_text(
                        art.raw_summary or art.title, max_words=80
                    )
                art.ai_summary = normalize_stored_ai_summary(art.ai_summary)

        if DEDUPLICATION_ENABLED and user_id is not None:
            delivered_all_members = [
                member for _primary, members in chosen_clusters for member in members
            ]
            _record_deliveries_for_user(session, user_id, delivered_all_members, now)

        session.commit()

        lines: List[str] = list(_latest_news_heading_lines(max_items))
        for primary_art, members in chosen_clusters:
            art = primary_art
            category = category_label_for_article(art)

            escaped_title = escape_html(art.title)
            if scheduled_push:
                body = (art.ai_summary or "").strip()
                escaped_summary = escape_html(normalize_stored_ai_summary(body))
            else:
                raw_sum = art.ai_summary or _fallback_summary_from_text(
                    art.raw_summary or art.title, max_words=80
                )
                escaped_summary = escape_html(normalize_stored_ai_summary(raw_sum or ""))
            escaped_category = escape_html(category)

            lines.append(f"<blockquote>[{escaped_category}] <b>{escaped_title}</b></blockquote>")
            lines.append(escaped_summary)
            lines.append("")
            lines.append(
                _format_sources_html_from_article_cluster(
                    members,
                    escape_html=escape_html,
                )
            )
            lines.append("────────────")

        if lines and lines[-1] == "────────────":
            lines.pop()
        return "\n".join(lines)


def _truncate_text(text: str, max_chars: int) -> str:
    cleaned = " ".join((text or "").split())
    if not cleaned:
        return ""
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[:max_chars].rstrip(" ,.;:!?") + "..."


def get_todays_news_digest_for_user(telegram_id: int, max_articles: int = 6) -> str:
    """
    Conversational mode: generate ONE digest for "today's news" from local DB (SQLite).
    Uses Ollama to create a bullet-point digest from per-article snippets.
    """
    from src.core.user_service import get_user_preference
    from src.ai.summarizer import summarize_digest

    preference = get_user_preference(telegram_id)
    categories_filter = preference.categories if preference else ""
    locations_filter = preference.locations if preference else ""
    area_keywords_filter = preference.area_keywords if preference else ""

    now = datetime.utcnow()
    start_of_today = datetime(now.year, now.month, now.day)

    with SessionLocal() as session:
        candidates: List[NewsArticle] = list(
            session.execute(
                select(NewsArticle)
                .where(NewsArticle.created_at >= start_of_today)
                .order_by(NewsArticle.created_at.desc())
            ).scalars().all()
        )

    if not candidates:
        # DB might not be warmed up yet; fall back to RSS for a "today" digest.
        from src.core.config import RSS_FEEDS
        from src.scrapers.rss_reader import fetch_latest_items

        from src.scrapers.rss_reader import RssItem

        eff_lim = effective_rss_limit_per_feed(10)
        items = _fetch_combined_latest_items(limit_per_feed=eff_lim, max_age_hours=24)
        # Prefer items published today (UTC). If published is missing, keep them as fallback.
        todays: list[RssItem] = []
        start_next_day = start_of_today + timedelta(days=1)
        for it in items:
            if not _rss_item_prefilter_for_user_pref(it, area_keywords_filter=area_keywords_filter):
                continue
            if it.published and (it.published < start_of_today or it.published >= start_next_day):
                continue
            todays.append(it)

        if not todays:
            todays = [
                it
                for it in items
                if _rss_item_prefilter_for_user_pref(it, area_keywords_filter=area_keywords_filter)
            ][: max_articles * 2]

        if not todays:
            return ""

        # Apply same category/location filtering and ranking.
        ranked: List[tuple[int, datetime, RssItem]] = []
        for it in todays:
            snippet_for_rank = it.summary or it.title or ""
            if not _matches_user_category_filter(
                stored_category=None,
                state=None,
                title=it.title or "",
                summary=it.summary or "",
                categories_filter=categories_filter,
            ):
                continue
            loc_d, st_d = extract_location_and_state(it.title or "", it.summary or "")
            if not _row_matches_user_locations(
                title=it.title or "",
                summary=it.summary,
                state=st_d,
                location=loc_d,
                locations_filter=locations_filter,
            ):
                continue
            rank = _geo_priority_rank(
                title=it.title or "",
                summary=snippet_for_rank,
                locations_filter=locations_filter,
                area_keywords_filter=area_keywords_filter,
                state=st_d,
                location=loc_d,
            )
            published_ts = it.published or start_of_today
            ranked.append((rank, published_ts, it))

        ranked.sort(key=lambda t: (t[0], -t[1].timestamp()))
        ranked_items = [t[2] for t in ranked]
        chosen_items = _deduplicate_items(ranked_items, max_items=max_articles)
        if not chosen_items:
            return ""

        items_text_lines: List[str] = []
        for it in chosen_items:
            title = (it.title or "").replace("\n", " ").strip()[:220]
            snippet = _truncate_text(it.summary or it.title or "", max_chars=550)
            if title:
                items_text_lines.append(f"- {title}\n  {snippet}")

        items_text = "\n".join(items_text_lines).strip()
        digest = summarize_digest(items_text, max_words=160) if items_text else None
        if not digest:
            digest = items_text

        def escape_html(text: str) -> str:
            return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        safe_digest = escape_html(strip_markdown_artifacts_for_plain_text(digest))
        return (
            "<b>🗞️ Today's news summary</b>\n"
            f"{safe_digest}\n\n"
            "<i>Generated using Ollama (DB fallback from RSS).</i>"
        )

    ranked: List[tuple[int, datetime, NewsArticle]] = []
    for art in candidates:
        raw_summary = art.raw_summary or ""
        summary_for_rank = raw_summary or art.ai_summary or art.title or ""

        if not _db_article_eligible_for_user_pref(
            art,
            categories_filter=categories_filter,
            area_keywords_filter=area_keywords_filter,
            locations_filter=locations_filter,
        ):
            continue

        rank = _geo_priority_rank(
            title=art.title or "",
            summary=summary_for_rank or "",
            locations_filter=locations_filter,
            area_keywords_filter=area_keywords_filter,
            state=getattr(art, "state", None),
            location=getattr(art, "location", None),
        )
        ranked.append((rank, art.created_at, art))

    ranked.sort(key=lambda t: (t[0], -t[1].timestamp()))
    ranked_articles: List[NewsArticle] = [t[2] for t in ranked]
    chosen: List[NewsArticle] = _dedup_ranked_articles_cross_source(
        ranked_articles, max_items=max_articles
    )

    if not chosen:
        return ""

    # Build compact input for the LLM (titles + already-stored snippets).
    items_text_lines: List[str] = []
    for art in chosen:
        title = (art.title or "").replace("\n", " ").strip()[:220]
        snippet = (art.ai_summary or art.raw_summary or art.title or "")
        snippet = _truncate_text(snippet, max_chars=550)
        if not title:
            continue
        items_text_lines.append(f"- {title}\n  {snippet}")

    items_text = "\n".join(items_text_lines).strip()

    digest = summarize_digest(items_text, max_words=160)
    if not digest:
        # Fallback: use the compact item list.
        digest = items_text

    def escape_html(text: str) -> str:
        return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    safe_digest = escape_html(strip_markdown_artifacts_for_plain_text(digest))
    return (
        "<b>🗞️ Today's news summary</b>\n"
        f"{safe_digest}\n\n"
        "<i>Generated from your local database using Ollama.</i>"
    )


def _question_intent_keywords(user_text: str) -> list[str]:
    text = (user_text or "").lower()
    intent_map = {
        "crime": ["crime", "rob", "theft", "murder", "assault", "police"],
        "weather": ["weather", "rain", "storm", "flood", "hot", "temperature"],
        "politics": ["politic", "election", "minister", "policy", "government"],
        "transport": ["traffic", "road", "jam", "accident", "transport", "closure"],
    }
    out: list[str] = []
    for _, keys in intent_map.items():
        if any(k in text for k in keys):
            out.extend(keys)
    return out


def _article_matches_question_intent(art: NewsArticle, user_text: str) -> bool:
    keys = _question_intent_keywords(user_text)
    if not keys:
        return True
    blob = (
        f"{art.title or ''} {art.ai_summary or ''} {art.raw_summary or ''} {art.category or ''}"
    ).lower()
    return any(k in blob for k in keys)


def _question_location_keywords(user_text: str) -> list[str]:
    text = f" {(user_text or '').lower()} "
    matched: list[str] = []
    for canonical, aliases in SARAWAK_LOCATION_ALIASES.items():
        for alias in aliases:
            alias_token = alias.strip().lower()
            if not alias_token:
                continue
            if f" {alias_token} " in text:
                matched.append(canonical)
                break
    return list(dict.fromkeys(matched))


def _article_matches_question_location(art: NewsArticle, user_text: str) -> bool:
    loc_keys = _question_location_keywords(user_text)
    if not loc_keys:
        return True
    derived_loc, _state = extract_location_and_state(
        art.title or "",
        art.ai_summary or art.raw_summary or "",
    )
    # Prefer freshly derived location from title/summary (handles stale DB values).
    if derived_loc:
        return derived_loc in loc_keys

    art_loc = (getattr(art, "location", None) or "").strip().lower()
    return bool(art_loc and art_loc in loc_keys)


def _article_is_relevant_to_question(art: NewsArticle, user_text: str) -> bool:
    return _article_matches_question_intent(art, user_text) and _article_matches_question_location(
        art, user_text
    )


def _format_news_agent_html(
    *,
    answer: str,
    evidence_rows: list[dict[str, str]],
) -> str:
    def escape_html(text: str) -> str:
        return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    lines: list[str] = [
        "<b>🗞️ News agent</b>",
        escape_html(strip_markdown_artifacts_for_plain_text(answer)),
    ]
    if evidence_rows:
        lines.append("")
        lines.append("<b>Relevant news:</b>")
        for row in evidence_rows[:2]:
            title = escape_html((row.get("title") or "").strip())
            summary = escape_html(
                _truncate_text(
                    normalize_stored_ai_summary(row.get("summary") or ""),
                    max_chars=260,
                )
            )
            source = escape_html((row.get("source") or "").strip())
            link = escape_html((row.get("link") or "").strip())
            category = escape_html((row.get("category") or "Local").strip() or "Local")
            if not title:
                continue
            lines.append(f"<blockquote>[{category}] <b>{title}</b></blockquote>")
            if summary:
                lines.append(summary)
            lines.append("")
            if source and link:
                lines.append(f'Sources: <a href="{link}">{source}</a>')
            elif link:
                lines.append(f"URL: {link}")
            lines.append("────────────")
        if lines and lines[-1] == "────────────":
            lines.pop()
    return "\n".join(lines).strip()


def _format_no_related_news_html(
    *,
    fallback_rows: list[dict[str, str]],
) -> str:
    def escape_html(text: str) -> str:
        return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    lines = [
        "<b>🗞️ News agent</b>",
        "I couldn't find related news for your question in the current database.",
    ]
    if fallback_rows:
        lines.append("")
        lines.append("<b>Other recent news (optional):</b>")
        for row in fallback_rows[:2]:
            title = escape_html((row.get("title") or "").strip())
            summary = escape_html(
                _truncate_text(
                    normalize_stored_ai_summary(row.get("summary") or ""),
                    max_chars=220,
                )
            )
            source = escape_html((row.get("source") or "").strip())
            link = escape_html((row.get("link") or "").strip())
            category = escape_html((row.get("category") or "Local").strip() or "Local")
            if not title:
                continue
            lines.append(f"<blockquote>[{category}] <b>{title}</b></blockquote>")
            if summary:
                lines.append(summary)
            lines.append("")
            if source and link:
                lines.append(f'Sources: <a href="{link}">{source}</a>')
            elif link:
                lines.append(f"URL: {link}")
            lines.append("────────────")
        if lines and lines[-1] == "────────────":
            lines.pop()
    return "\n".join(lines).strip()


def get_news_agent_response_for_user(telegram_id: int, user_text: str) -> str:
    """
    "News agent" mode:
    - Uses DB (today/last 24h) to build a small evidence set.
    - Uses Ollama to answer the user's question strictly from those items.
    """
    from src.core.user_service import get_user_preference
    from src.ai.summarizer import answer_news_question
    from src.ai.retriever import semantic_rank_articles

    preference = get_user_preference(telegram_id)
    categories_filter = preference.categories if preference else ""
    locations_filter = preference.locations if preference else ""
    area_keywords_filter = preference.area_keywords if preference else ""

    now = datetime.utcnow()
    cutoff = now - timedelta(hours=24)

    with SessionLocal() as session:
        candidates: List[NewsArticle] = list(
            session.execute(
                select(NewsArticle)
                .where(NewsArticle.created_at >= cutoff)
                .order_by(NewsArticle.created_at.desc())
            ).scalars().all()
        )

    # If the DB isn't warmed up, fetch RSS as evidence.
    if not candidates:
        eff_lim = effective_rss_limit_per_feed(12)
        items = _fetch_combined_latest_items(limit_per_feed=eff_lim, max_age_hours=24)
        # Use title+snippet for evidence set.
        evidence_lines: List[str] = []
        for it in items[:12]:
            title = it.title or ""
            snippet = it.summary or it.title or ""
            if not title:
                continue
            if not _rss_item_prefilter_for_user_pref(it, area_keywords_filter=area_keywords_filter):
                continue
            if not _matches_user_category_filter(
                stored_category=None,
                state=None,
                title=title,
                summary=it.summary or "",
                categories_filter=categories_filter,
            ):
                continue
            loc_e, st_e = extract_location_and_state(title, it.summary or "")
            if not _row_matches_user_locations(
                title=title,
                summary=it.summary,
                state=st_e,
                location=loc_e,
                locations_filter=locations_filter,
            ):
                continue
            rank = _geo_priority_rank(
                title=title,
                summary=snippet,
                locations_filter=locations_filter,
                area_keywords_filter=area_keywords_filter,
                state=st_e,
                location=loc_e,
            )
            # rank is only used to choose order; we don't need full ranking here.
            evidence_lines.append(f"- {title}\n  {_truncate_text(snippet, max_chars=450)}")

        items_text = "\n".join(evidence_lines).strip()
        answer = answer_news_question(user_text, items_text)
        evidence_rows = []
        fallback_rows = []
        for it in items:
            row = {
                "title": it.title or "",
                "summary": it.summary or it.title or "",
                "source": _get_source_name(it.source),
                "link": it.link or "",
                "category": _get_category_with_llm_fallback(it.title or "", it.summary or ""),
            }
            fallback_rows.append(row)
            pseudo = NewsArticle(
                title=row["title"],
                raw_summary=row["summary"],
                ai_summary=row["summary"],
                source=row["source"],
                link=row["link"],
                category=row["category"],
            )
            if _article_is_relevant_to_question(pseudo, user_text):
                evidence_rows.append(row)
        if not answer or not evidence_rows:
            return _format_no_related_news_html(fallback_rows=fallback_rows)
        return _format_news_agent_html(answer=answer, evidence_rows=evidence_rows)

    ranked: List[tuple[int, datetime, NewsArticle]] = []
    for art in candidates:
        summary_for_rank = art.raw_summary or art.ai_summary or art.title or ""
        if not _db_article_eligible_for_user_pref(
            art,
            categories_filter=categories_filter,
            area_keywords_filter=area_keywords_filter,
            locations_filter=locations_filter,
        ):
            continue
        rank = _geo_priority_rank(
            title=art.title or "",
            summary=summary_for_rank or "",
            locations_filter=locations_filter,
            area_keywords_filter=area_keywords_filter,
            state=getattr(art, "state", None),
            location=getattr(art, "location", None),
        )
        ranked.append((rank, art.created_at, art))

    ranked.sort(key=lambda t: (t[0], -t[1].timestamp()))
    intent_filtered = [t for t in ranked if _article_matches_question_intent(t[2], user_text)]
    if intent_filtered:
        ranked = intent_filtered

    fallback_chosen = [t[2] for t in ranked[:10]]
    candidate_pool = [t[2] for t in ranked[:RAG_NEWS_CANDIDATE_POOL]]
    chosen = fallback_chosen
    if RAG_ENABLED and candidate_pool:
        semantic_hits = semantic_rank_articles(
            query=user_text,
            articles=candidate_pool,
            top_k=RAG_NEWS_TOP_K,
        )
        if semantic_hits:
            chosen = semantic_hits
    if not chosen:
        fallback_rows = []
        for art in fallback_chosen[:2]:
            fallback_rows.append(
                {
                    "title": art.title or "",
                    "summary": art.ai_summary or art.raw_summary or art.title or "",
                    "source": _get_source_name(art.source),
                    "link": art.link or "",
                    "category": category_label_for_article(art),
                }
            )
        return _format_no_related_news_html(fallback_rows=fallback_rows)

    evidence_lines: List[str] = []
    for art in chosen:
        title = (art.title or "").replace("\n", " ").strip()[:220]
        snippet = (art.ai_summary or art.raw_summary or art.title or "").strip()
        snippet = _truncate_text(snippet, max_chars=450)
        if title and snippet:
            evidence_lines.append(f"- {title}\n  {snippet}")

    items_text = "\n".join(evidence_lines).strip()
    answer = answer_news_question(user_text, items_text)
    evidence_rows = []
    for art in chosen:
        if not _article_is_relevant_to_question(art, user_text):
            continue
        evidence_rows.append(
            {
                "title": art.title or "",
                "summary": art.ai_summary or art.raw_summary or art.title or "",
                "source": _get_source_name(art.source),
                "link": art.link or "",
                "category": category_label_for_article(art),
            }
        )
    if not answer or not evidence_rows:
        fallback_rows = []
        for art in chosen[:2]:
            fallback_rows.append(
                {
                    "title": art.title or "",
                    "summary": art.ai_summary or art.raw_summary or art.title or "",
                    "source": _get_source_name(art.source),
                    "link": art.link or "",
                    "category": category_label_for_article(art),
                }
            )
        return _format_no_related_news_html(fallback_rows=fallback_rows)
    return _format_news_agent_html(answer=answer, evidence_rows=evidence_rows)
