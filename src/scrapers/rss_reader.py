from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, List

import feedparser


@dataclass
class RssItem:
    title: str
    link: str
    source: str
    summary: str | None = None
    published: datetime | None = None


def fetch_latest_items(
    feeds: Iterable[str],
    limit_per_feed: int = 15,  # Increased to get more articles from past day
    max_age_hours: int = 24,  # Only fetch articles from last 24 hours
) -> List[RssItem]:
    """
    Fetch latest RSS items from the given feeds.
    Filters to only include articles from the past max_age_hours.
    """
    items: List[RssItem] = []
    cutoff_time = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)

    for feed_url in feeds:
        try:
            parsed = feedparser.parse(feed_url)
            for entry in parsed.entries[:limit_per_feed]:
                title = getattr(entry, "title", "").strip()
                link = getattr(entry, "link", "").strip()
                # Many feeds expose 'summary' or 'description'. Fallback to empty string.
                raw_summary = getattr(entry, "summary", "") or getattr(
                    entry, "description", ""
                )
                summary = raw_summary.strip() or None
                
                if not title or not link:
                    continue
                
                # Try to parse published date
                published = None
                if hasattr(entry, "published_parsed") and entry.published_parsed:
                    try:
                        published = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
                    except Exception:
                        pass
                elif hasattr(entry, "updated_parsed") and entry.updated_parsed:
                    try:
                        published = datetime(*entry.updated_parsed[:6], tzinfo=timezone.utc)
                    except Exception:
                        pass
                
                # Filter by date if we have it
                if published and published < cutoff_time:
                    continue  # Skip articles older than max_age_hours
                
                items.append(
                    RssItem(
                        title=title,
                        link=link,
                        source=feed_url,
                        summary=summary,
                        published=published,
                    )
                )
        except Exception as e:
            # If a feed fails, continue with other feeds
            print(f"Error fetching feed {feed_url}: {e}")
            continue

    return items


