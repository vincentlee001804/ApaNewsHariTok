from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List

import feedparser


@dataclass
class RssItem:
    title: str
    link: str
    source: str
    summary: str | None = None


def fetch_latest_items(
    feeds: Iterable[str],
    limit_per_feed: int = 3,
) -> List[RssItem]:
    """
    Fetch latest RSS items from the given feeds.
    For now we only extract title and link, which is enough to display in /latest.
    """
    items: List[RssItem] = []

    for feed_url in feeds:
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
            items.append(
                RssItem(
                    title=title,
                    link=link,
                    source=feed_url,
                    summary=summary,
                )
            )

    return items


