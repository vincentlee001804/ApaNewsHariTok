from __future__ import annotations

from datetime import datetime, timedelta
from typing import List

from sqlalchemy import select

from src.ai.summarizer import summarize
from src.core.config import RSS_FEEDS
from src.core.models import NewsArticle
from src.scrapers.rss_reader import RssItem, fetch_latest_items
from src.storage.database import SessionLocal


def _deduplicate_items(items: List[RssItem], max_items: int) -> List[RssItem]:
    seen_links = set()
    unique_items: List[RssItem] = []
    for item in items:
        if item.link in seen_links:
            continue
        seen_links.add(item.link)
        unique_items.append(item)
        if len(unique_items) >= max_items:
            break
    return unique_items


def _matches_category_filter(title: str, summary: str, categories: str) -> bool:
    """
    Check if a news item matches the user's category filter.
    If categories is empty, all items match.
    Otherwise, check if any category keyword appears in title or summary (case-insensitive).
    """
    if not categories or categories.strip() == "":
        return True

    category_list = [cat.strip().lower() for cat in categories.split(",") if cat.strip()]
    if not category_list:
        return True

    text_to_search = (title + " " + (summary or "")).lower()

    for category in category_list:
        if category in text_to_search:
            return True

    return False


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


def _extract_category(title: str, summary: str | None) -> str:
    """
    Extract a category tag from the news title/summary.
    Returns a category like "Local", "Sports", "Politics", "Business", etc.
    """
    text = (title + " " + (summary or "")).lower()

    # Simple keyword-based categorization
    if any(word in text for word in ["sport", "football", "badminton", "tennis", "olympic"]):
        return "Sports"
    elif any(word in text for word in ["politic", "minister", "government", "parliament", "election"]):
        return "Politics"
    elif any(word in text for word in ["business", "economy", "trade", "market", "stock"]):
        return "Business"
    elif any(word in text for word in ["sarawak", "kuching", "miri", "sibu", "borneo"]):
        return "Local"
    elif any(word in text for word in ["flood", "accident", "fire", "emergency", "disaster"]):
        return "Emergency"
    elif any(word in text for word in ["health", "hospital", "medical", "covid", "disease"]):
        return "Health"
    elif any(word in text for word in ["education", "school", "university", "student"]):
        return "Education"
    else:
        return "General"


def _get_source_name(source_url: str) -> str:
    """
    Extract a friendly source name from the RSS feed URL.
    """
    source_mapping = {
        "sarawaktribune.com": "Sarawak Tribune",
        "seehua.com": "See Hua Daily News",
        "theborneopost.com": "Borneo Post Online",
    }

    for domain, name in source_mapping.items():
        if domain in source_url.lower():
            return name

    # Fallback: extract domain name
    try:
        from urllib.parse import urlparse
        parsed = urlparse(source_url)
        domain = parsed.netloc.replace("www.", "")
        return domain.split(".")[0].title() if domain else "Unknown Source"
    except Exception:
        return "Unknown Source"


def get_latest_news_text(max_items: int = 3) -> str:
    """
    Fetch latest items from configured RSS feeds and format them
    into a Markdown string suitable for Telegram, including
    a short AI-generated summary for each item where possible.
    """
    items: List[RssItem] = fetch_latest_items(RSS_FEEDS, limit_per_feed=3)
    unique_items = _deduplicate_items(items, max_items=max_items)

    if not unique_items:
        return (
            "I couldn't fetch any news items right now.\n"
            "_This might be a temporary network issue or the sources are unavailable._"
        )

    # Use the database to skip headlines that were already sent before.
    # Once sent, articles are never sent again (permanent duplicate prevention).
    to_display: List[RssItem] = []
    now = datetime.utcnow()

    with SessionLocal() as session:
        for item in unique_items:
            existing: NewsArticle | None = session.execute(
                select(NewsArticle).where(NewsArticle.link == item.link)
            ).scalar_one_or_none()

            if existing and existing.last_sent_at is not None:
                # This article has already been sent at least once; skip it forever.
                continue

            if not existing:
                existing = NewsArticle(
                    title=item.title,
                    link=item.link,
                    source=item.source,
                    raw_summary=item.summary,
                    last_sent_at=now,
                )
                session.add(existing)
            else:
                existing.last_sent_at = now

            to_display.append(item)

        session.commit()

    if not to_display:
        return (
            "No new headlines since your last request.\n"
            "_You are up to date with the latest news from these sources._"
        )

    lines: List[str] = ["*Latest local news with AI summaries:*"]

    for item in to_display:
        # Extract category
        category = _extract_category(item.title, item.summary)

        # Get AI summary
        source_text = item.summary or item.title
        ai_summary = summarize(source_text, max_words=40)

        if not ai_summary:
            ai_summary = "(No AI summary available right now.)"

        # Get source name
        source_name = _get_source_name(item.source)

        # Format according to specification:
        # [Category][Short title]
        # [Summary]
        # [Source name]([link])
        lines.append(f"[{category}] {item.title}")
        lines.append(ai_summary)
        lines.append(f"[{source_name}]({item.link})")
        lines.append("")  # Empty line between items

    # Remove the last empty line and add footer
    if lines and lines[-1] == "":
        lines.pop()

    lines.append("\n_Summaries generated locally by the LLM (no external AI APIs used)._")
    return "\n".join(lines)


def get_latest_news_text_for_user(telegram_id: int, max_items: int = 3) -> str:
    """
    Fetch latest items from configured RSS feeds, filtered by user preferences,
    and format them into a Markdown string suitable for Telegram.
    """
    from src.core.user_service import get_user_preference

    preference = get_user_preference(telegram_id)
    categories_filter = preference.categories if preference else ""
    locations_filter = preference.locations if preference else ""

    # Fetch more items to get articles from the past 24 hours
    items: List[RssItem] = fetch_latest_items(RSS_FEEDS, limit_per_feed=15, max_age_hours=24)
    unique_items = _deduplicate_items(items, max_items=max_items * 3)  # Fetch more to filter

    if not unique_items:
        return (
            "I couldn't fetch any news items right now.\n"
            "_This might be a temporary network issue or the sources are unavailable._"
        )

    # Apply category and location filters
    filtered_items: List[RssItem] = []
    for item in unique_items:
        matches_category = _matches_category_filter(item.title, item.summary or "", categories_filter)
        matches_location = _matches_location_filter(item.title, item.summary or "", locations_filter)

        if matches_category and matches_location:
            filtered_items.append(item)
        if len(filtered_items) >= max_items:
            break

    if not filtered_items:
        return (
            "No news items match your current filters (categories/locations).\n"
            "_Try adjusting your settings with /settings to see more news._"
        )

    # Use the database to skip headlines that were already sent before.
    # Once sent, articles are never sent again (permanent duplicate prevention).
    to_display: List[RssItem] = []
    now = datetime.utcnow()

    with SessionLocal() as session:
        for item in filtered_items:
            existing: NewsArticle | None = session.execute(
                select(NewsArticle).where(NewsArticle.link == item.link)
            ).scalar_one_or_none()

            if existing and existing.last_sent_at is not None:
                # This article has already been sent at least once; skip it forever.
                continue

            if not existing:
                existing = NewsArticle(
                    title=item.title,
                    link=item.link,
                    source=item.source,
                    raw_summary=item.summary,
                    last_sent_at=now,
                )
                session.add(existing)
            else:
                existing.last_sent_at = now

            to_display.append(item)

        session.commit()

    if not to_display:
        return (
            "No new headlines since your last request.\n"
            "_You are up to date with the latest news from these sources._"
        )

    lines: List[str] = ["*Latest local news with AI summaries:*"]

    for item in to_display:
        # Extract category
        category = _extract_category(item.title, item.summary)

        # Get AI summary
        source_text = item.summary or item.title
        ai_summary = summarize(source_text, max_words=40)

        if not ai_summary:
            ai_summary = "(No AI summary available right now.)"

        # Get source name
        source_name = _get_source_name(item.source)

        # Format according to specification:
        # [Category][Short title]
        # [Summary]
        # [Source name]([link])
        lines.append(f"[{category}] {item.title}")
        lines.append(ai_summary)
        lines.append(f"[{source_name}]({item.link})")
        lines.append("")  # Empty line between items

    # Remove the last empty line and add footer
    if lines and lines[-1] == "":
        lines.pop()

    lines.append("\n_Summaries generated locally by the LLM (no external AI APIs used)._")
    return "\n".join(lines)

