from __future__ import annotations

from typing import List

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from src.core.services import _get_source_name  # reuse existing mapping
from src.core.config import RSS_FEEDS, TELEGRAM_SOURCE_CHANNELS
from src.core.local_keywords import matches_local_interest
from src.core.location_extractor import extract_location_and_state
from src.core.models import NewsArticle
from src.core.rss_limits import effective_rss_limit_per_feed
from src.scrapers.rss_reader import RssItem, fetch_latest_items
from src.scrapers.telegram_reader import (
    canonical_link_for_news_item,
    fetch_latest_telegram_items,
)
from src.storage.database import SessionLocal


def _batch_dedup_key(link: str) -> str:
    if link.lower().startswith("https://t.me/"):
        return link.lower()
    return link


def prefetch_latest_articles_to_db(
    *,
    limit_per_feed: int = 15,
    max_age_hours: int = 24,
) -> int:
    """
    Fetch recent RSS items plus optional Telegram channels (see TELEGRAM_SOURCE_CHANNELS)
    and store them into the database.

    Runs on the bot's repeating prefetch job (see PREFETCH_INTERVAL_MINUTES in bot_main).

    Deduplication is enforced by the unique constraint on NewsArticle.link.
    Returns the number of newly inserted rows.
    """
    eff_limit = effective_rss_limit_per_feed(limit_per_feed)
    items: List[RssItem] = fetch_latest_items(
        RSS_FEEDS,
        limit_per_feed=eff_limit,
        max_age_hours=max_age_hours,
    )
    telegram_items: List[RssItem] = fetch_latest_telegram_items(
        TELEGRAM_SOURCE_CHANNELS,
        limit_per_source=eff_limit,
        max_age_hours=max_age_hours,
    )
    if telegram_items:
        items.extend(telegram_items)

    if not items:
        return 0

    inserted = 0
    with SessionLocal() as session:
        seen_batch: set[str] = set()
        for item in items:
            if not matches_local_interest(item.title, item.summary):
                continue
            link = canonical_link_for_news_item(item)
            bkey = _batch_dedup_key(link)
            if bkey in seen_batch:
                continue
            seen_batch.add(bkey)

            if link.lower().startswith("https://t.me/"):
                already = session.execute(
                    select(NewsArticle.id).where(
                        func.lower(NewsArticle.link) == link.lower()
                    )
                ).scalar_one_or_none()
                if already is not None:
                    continue

            location, state = extract_location_and_state(item.title, item.summary)
            article = NewsArticle(
                title=item.title,
                link=link,
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

