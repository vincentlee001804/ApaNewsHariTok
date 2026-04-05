from __future__ import annotations

from typing import List

from sqlalchemy.exc import IntegrityError

from src.core.services import _get_source_name  # reuse existing mapping
from src.core.config import RSS_FEEDS
from src.core.local_keywords import matches_local_interest
from src.core.location_extractor import extract_location_and_state
from src.core.models import NewsArticle
from src.core.rss_limits import effective_rss_limit_per_feed
from src.scrapers.rss_reader import RssItem, fetch_latest_items
from src.storage.database import SessionLocal


def prefetch_latest_articles_to_db(
    *,
    limit_per_feed: int = 15,
    max_age_hours: int = 24,
) -> int:
    """
    Fetch recent RSS items and store them into the database.

    Deduplication is enforced by the unique constraint on NewsArticle.link.
    Returns the number of newly inserted rows.
    """
    eff_limit = effective_rss_limit_per_feed(limit_per_feed)
    items: List[RssItem] = fetch_latest_items(
        RSS_FEEDS,
        limit_per_feed=eff_limit,
        max_age_hours=max_age_hours,
    )

    if not items:
        return 0

    inserted = 0
    with SessionLocal() as session:
        for item in items:
            if not matches_local_interest(item.title, item.summary):
                continue
            location, state = extract_location_and_state(item.title, item.summary)
            article = NewsArticle(
                title=item.title,
                link=item.link,
                source=_get_source_name(item.source),
                raw_summary=item.summary,
                location=location,
                state=state,
            )
            session.add(article)
            try:
                session.commit()
                inserted += 1
            except IntegrityError:
                session.rollback()  # duplicate link
            except Exception:
                session.rollback()
                raise

    return inserted

