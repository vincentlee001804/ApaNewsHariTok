from __future__ import annotations

from datetime import datetime, timedelta
from typing import List

from sqlalchemy import select

from src.ai.summarizer import classify_category, summarize
from src.core.config import DEDUPLICATION_ENABLED, RSS_FEEDS
from src.core.models import NewsArticle
from src.scrapers.rss_reader import RssItem, fetch_latest_items
from src.storage.database import SessionLocal


def _deduplicate_items(items: List[RssItem], max_items: int) -> List[RssItem]:
    """
    Deduplicate items by link, keeping the first occurrence.
    Items should be pre-sorted by date (newest first) to ensure latest news is prioritized.
    """
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


def _matches_area_keywords_filter(text: str, area_keywords: str) -> bool:
    """
    Free-text keyword match for very specific areas (roads, taman, kampung).

    - If area_keywords is empty: match everything.
    - Otherwise: match if ANY keyword appears in text (case-insensitive).
    """
    if not area_keywords or area_keywords.strip() == "":
        return True

    keywords = [k.strip().lower() for k in area_keywords.split(",") if k.strip()]
    if not keywords:
        return True

    haystack = (text or "").lower()
    return any(k in haystack for k in keywords)


def _fallback_summary_from_text(text: str, max_words: int = 50) -> str:
    """
    Deterministic fallback summary when LLM output is unavailable/unreliable.
    """
    cleaned = " ".join((text or "").split())
    if not cleaned:
        return "(No summary available right now.)"

    words = cleaned.split()
    if len(words) <= max_words:
        return cleaned
    return " ".join(words[:max_words]).rstrip(" ,;:.") + "..."


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
) -> int:
    """
    Geographic relevance rank (lower is higher priority):

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

    if locations_filter.strip() and _matches_location_filter(title, summary, locations_filter):
        return 1

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
    
    # Default to "Local" for general Sarawak news (checked last since location names appear in most news)
    # Only use "Local" if no other category matched
    if any(word in text for word in ["sarawak", "kuching", "miri", "sibu", "borneo", "bintulu", "samarahan"]):
        return "Local"
    
    # Fallback to "General" if nothing matches
    return "General"


def _get_category_with_llm_fallback(title: str, summary: str | None) -> str:
    """
    Prefer LLM-based classification (more accurate), fallback to keyword rules.
    """
    llm_text = (title + "\n" + (summary or "")).strip()
    llm_category = classify_category(llm_text)
    if llm_category:
        return llm_category
    return _extract_category(title, summary)


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


def get_latest_news_text(max_items: int = 3) -> str:
    """
    Fetch latest items from configured RSS feeds and format them
    into a Markdown string suitable for Telegram, including
    a short AI-generated summary for each item where possible.
    """
    items: List[RssItem] = fetch_latest_items(RSS_FEEDS, limit_per_feed=3)
    
    # Sort by date (newest first) across all sources to mix news from different feeds
    sorted_items = _sort_items_by_date(items)
    
    # Deduplicate after sorting to ensure we get latest news from any source
    unique_items = _deduplicate_items(sorted_items, max_items=max_items)

    if not unique_items:
        return (
            "I couldn't fetch any news items right now.\n"
            "<i>This might be a temporary network issue or the sources are unavailable.</i>"
        )

    # Deduplication toggle:
    # - Enabled (default): once sent, never sent again (permanent duplicate prevention)
    # - Disabled: always show items (useful for testing message formatting)
    to_display: List[RssItem] = []
    now = datetime.utcnow()

    if not DEDUPLICATION_ENABLED:
        to_display = unique_items[:max_items]
    else:
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
                if len(to_display) >= max_items:
                    break

            session.commit()

    if not to_display:
        return (
            "No new headlines since your last request.\n"
            "<i>You are up to date with the latest news from these sources.</i>"
        )

    lines: List[str] = ["<b>Latest local news with AI summaries:</b>"]

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
        
        ai_summary = summarize(source_text, max_words=50, title=item.title)
        if not ai_summary:
            ai_summary = _fallback_summary_from_text(item.summary or item.title, max_words=50)

        # Get source name
        source_name = _get_source_name(item.source)

        # Escape HTML special characters in title and summary
        def escape_html(text: str) -> str:
            return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        escaped_title = escape_html(item.title)
        escaped_summary = escape_html(ai_summary)
        escaped_source = escape_html(source_name)

        # Format according to specification:
        # <blockquote>[Category] <b>Title</b></blockquote> (headline in blockquote for visual distinction)
        # Summary (plain text)
        # <a href="link">Source name</a>
        escaped_category = escape_html(category)
        lines.append(f"<blockquote>[{escaped_category}] <b>{escaped_title}</b></blockquote>")
        lines.append(escaped_summary)
        lines.append(f'<a href="{item.link}">{escaped_source}</a>')
        lines.append("────────────")  # Visual separator between items

    # Remove the last empty line and add footer
    if lines and lines[-1] == "────────────":
        lines.pop()

    lines.append("\n<i>Summaries generated locally by the LLM (no external AI APIs used).</i>")
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
    area_keywords_filter = preference.area_keywords if preference else ""

    def escape_html(text: str) -> str:
        return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    # DB-first: use prefetched articles (fast + cached) and only call LLM when needed.
    now = datetime.utcnow()
    cutoff = now - timedelta(hours=24)

    with SessionLocal() as session:
        stmt = (
            select(NewsArticle)
            .where(NewsArticle.created_at >= cutoff)
            .order_by(NewsArticle.created_at.desc())
        )
        if DEDUPLICATION_ENABLED:
            stmt = stmt.where(NewsArticle.last_sent_at.is_(None))

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
            if not _matches_category_filter(art.title, art.raw_summary or "", categories_filter):
                continue
            rank = _geo_priority_rank(
                title=art.title,
                summary=summary_for_rank,
                locations_filter=locations_filter,
                area_keywords_filter=effective_area_keywords_filter,
            )
            ranked.append((rank, art.created_at, art))

        # Sort by rank (best first), then newest first
        ranked.sort(key=lambda t: (t[0], -t[1].timestamp()))

        chosen: List[NewsArticle] = [t[2] for t in ranked[:max_items]]

        if not chosen:
            # Fallback: live RSS fetch if DB has nothing yet
            items: List[RssItem] = fetch_latest_items(
                RSS_FEEDS, limit_per_feed=15, max_age_hours=24
            )
            sorted_items = _sort_items_by_date(items)
            unique_items = _deduplicate_items(sorted_items, max_items=max_items * 3)

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
                if not _matches_category_filter(item.title, item.summary or "", categories_filter):
                    continue
                rank = _geo_priority_rank(
                    title=item.title,
                    summary=item.summary or "",
                    locations_filter=locations_filter,
                    area_keywords_filter=effective_area_keywords_filter_rss,
                )
                published_ts = item.published.timestamp() if item.published else 0.0
                ranked_items.append((rank, published_ts, item))

            ranked_items.sort(key=lambda t: (t[0], -t[1]))
            filtered_items: List[RssItem] = [t[2] for t in ranked_items[:max_items]]

            if not filtered_items:
                return (
                    "No news items match your current filters (categories/locations).\n"
                    "<i>Try adjusting your settings with /settings to see more news.</i>"
                )

            # Deduplication behavior (unchanged for RSS fallback)
            to_display: List[RssItem] = []
            if not DEDUPLICATION_ENABLED:
                to_display = filtered_items[:max_items]
            else:
                for item in filtered_items:
                    existing: NewsArticle | None = session.execute(
                        select(NewsArticle).where(NewsArticle.link == item.link)
                    ).scalar_one_or_none()
                    if existing and existing.last_sent_at is not None:
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
                    if len(to_display) >= max_items:
                        break
                session.commit()

            if not to_display:
                return (
                    "No new headlines since your last request.\n"
                    "<i>You are up to date with the latest news from these sources.</i>"
                )

            from src.scrapers.article_scraper import extract_article_content

            lines: List[str] = ["<b>Latest local news with AI summaries:</b>"]
            for item in to_display:
                category = _get_category_with_llm_fallback(item.title, item.summary)
                article_text = extract_article_content(item.link)
                source_text = article_text or item.summary or item.title
                ai_summary = summarize(source_text, max_words=50, title=item.title)
                if not ai_summary:
                    ai_summary = _fallback_summary_from_text(item.summary or item.title, max_words=50)
                source_name = _get_source_name(item.source)

                escaped_title = escape_html(item.title)
                escaped_summary = escape_html(ai_summary)
                escaped_source = escape_html(source_name)
                escaped_category = escape_html(category)

                lines.append(f"<blockquote>[{escaped_category}] <b>{escaped_title}</b></blockquote>")
                lines.append(escaped_summary)
                lines.append(f'<a href="{item.link}">{escaped_source}</a>')
                lines.append("────────────")

            if lines and lines[-1] == "────────────":
                lines.pop()
            lines.append("\n<i>Summaries generated locally by the LLM (no external AI APIs used).</i>")
            return "\n".join(lines)

        # Ensure ai_summary exists (cache)
        from src.scrapers.article_scraper import extract_article_content

        for art in chosen:
            if art.ai_summary:
                continue
            article_text = extract_article_content(art.link)
            source_text = article_text or art.raw_summary or art.title
            art.ai_summary = summarize(source_text, max_words=50, title=art.title)
            if not art.ai_summary:
                art.ai_summary = _fallback_summary_from_text(
                    art.raw_summary or art.title, max_words=50
                )

        if DEDUPLICATION_ENABLED:
            for art in chosen:
                art.last_sent_at = now

        session.commit()

        lines: List[str] = ["<b>Latest local news with AI summaries:</b>"]
        for art in chosen:
            category = _get_category_with_llm_fallback(art.title, art.ai_summary or art.raw_summary)

            source_name = art.source
            if source_name.lower().startswith("http"):
                source_name = _get_source_name(source_name)

            escaped_title = escape_html(art.title)
            escaped_summary = escape_html(
                art.ai_summary
                or _fallback_summary_from_text(art.raw_summary or art.title, max_words=50)
            )
            escaped_source = escape_html(source_name)
            escaped_category = escape_html(category)

            lines.append(f"<blockquote>[{escaped_category}] <b>{escaped_title}</b></blockquote>")
            lines.append(escaped_summary)
            lines.append(f'<a href="{art.link}">{escaped_source}</a>')
            lines.append("────────────")

        if lines and lines[-1] == "────────────":
            lines.pop()
        lines.append("\n<i>Summaries generated locally by the LLM (no external AI APIs used).</i>")
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

        items = fetch_latest_items(RSS_FEEDS, limit_per_feed=10, max_age_hours=24)
        # Prefer items published today (UTC). If published is missing, keep them as fallback.
        todays: list[RssItem] = []
        start_next_day = start_of_today + timedelta(days=1)
        for it in items:
            if it.published and (it.published < start_of_today or it.published >= start_next_day):
                continue
            todays.append(it)

        if not todays:
            todays = items[: max_articles * 2]

        if not todays:
            return ""

        # Apply same category/location filtering and ranking.
        ranked: List[tuple[int, datetime, RssItem]] = []
        for it in todays:
            snippet_for_rank = it.summary or it.title or ""
            if not _matches_category_filter(it.title or "", it.summary or "", categories_filter):
                continue
            rank = _geo_priority_rank(
                title=it.title or "",
                summary=snippet_for_rank,
                locations_filter=locations_filter,
                area_keywords_filter=area_keywords_filter,
            )
            published_ts = it.published or start_of_today
            ranked.append((rank, published_ts, it))

        ranked.sort(key=lambda t: (t[0], -t[1].timestamp()))
        chosen_items = [t[2] for t in ranked[:max_articles]]
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

        safe_digest = escape_html(digest)
        return (
            "<b>🗞️ Today&apos;s news summary</b>\n"
            f"{safe_digest}\n\n"
            "<i>Generated using Ollama (DB fallback from RSS).</i>"
        )

    ranked: List[tuple[int, datetime, NewsArticle]] = []
    for art in candidates:
        raw_summary = art.raw_summary or ""
        summary_for_rank = raw_summary or art.ai_summary or art.title or ""

        if not _matches_category_filter(
            art.title or "",
            summary_for_rank or "",
            categories_filter,
        ):
            continue

        rank = _geo_priority_rank(
            title=art.title or "",
            summary=summary_for_rank or "",
            locations_filter=locations_filter,
            area_keywords_filter=area_keywords_filter,
        )
        ranked.append((rank, art.created_at, art))

    ranked.sort(key=lambda t: (t[0], -t[1].timestamp()))
    chosen: List[NewsArticle] = [t[2] for t in ranked[:max_articles]]

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

    safe_digest = escape_html(digest)
    return (
        "<b>🗞️ Today&apos;s news summary</b>\n"
        f"{safe_digest}\n\n"
        "<i>Generated from your local database using Ollama.</i>"
    )


def get_news_agent_response_for_user(telegram_id: int, user_text: str) -> str:
    """
    "News agent" mode:
    - Uses DB (today/last 24h) to build a small evidence set.
    - Uses Ollama to answer the user's question strictly from those items.
    """
    from src.core.user_service import get_user_preference
    from src.ai.summarizer import answer_news_question
    from src.scrapers.rss_reader import fetch_latest_items
    from src.core.config import RSS_FEEDS

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
        items = fetch_latest_items(RSS_FEEDS, limit_per_feed=12, max_age_hours=24)
        # Use title+snippet for evidence set.
        evidence_lines: List[str] = []
        for it in items[:12]:
            title = it.title or ""
            snippet = it.summary or it.title or ""
            if not title:
                continue
            if not _matches_category_filter(title, it.summary or "", categories_filter):
                continue
            rank = _geo_priority_rank(
                title=title,
                summary=snippet,
                locations_filter=locations_filter,
                area_keywords_filter=area_keywords_filter,
            )
            # rank is only used to choose order; we don't need full ranking here.
            evidence_lines.append(f"- {title}\n  {_truncate_text(snippet, max_chars=450)}")

        items_text = "\n".join(evidence_lines).strip()
        answer = answer_news_question(user_text, items_text)
        if not answer:
            return "<b>News agent</b>\nI couldn't find relevant information in the news items I have."

        def escape_html(text: str) -> str:
            return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        return f"<b>🗞️ News agent</b>\n{escape_html(answer)}"

    ranked: List[tuple[int, datetime, NewsArticle]] = []
    for art in candidates:
        summary_for_rank = art.raw_summary or art.ai_summary or art.title or ""
        if not _matches_category_filter(art.title or "", art.raw_summary or "", categories_filter):
            continue
        rank = _geo_priority_rank(
            title=art.title or "",
            summary=summary_for_rank or "",
            locations_filter=locations_filter,
            area_keywords_filter=area_keywords_filter,
        )
        ranked.append((rank, art.created_at, art))

    ranked.sort(key=lambda t: (t[0], -t[1].timestamp()))
    chosen = [t[2] for t in ranked[:10]]
    if not chosen:
        return "<b>🗞️ News agent</b>\nI couldn't find relevant information in the news items I have."

    evidence_lines: List[str] = []
    for art in chosen:
        title = (art.title or "").replace("\n", " ").strip()[:220]
        snippet = (art.ai_summary or art.raw_summary or art.title or "").strip()
        snippet = _truncate_text(snippet, max_chars=450)
        if title and snippet:
            evidence_lines.append(f"- {title}\n  {snippet}")

    items_text = "\n".join(evidence_lines).strip()
    answer = answer_news_question(user_text, items_text)
    if not answer:
        return "<b>🗞️ News agent</b>\nI couldn't find relevant information in the news items I have."

    def escape_html(text: str) -> str:
        return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    return f"<b>🗞️ News agent</b>\n{escape_html(answer)}"

