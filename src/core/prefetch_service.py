from __future__ import annotations

from typing import List

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from src.core.services import _get_source_name, backfill_ai_summaries_for_article_ids
from src.core.config import PREFETCH_AI_SUMMARY, RSS_FEEDS, TELEGRAM_SOURCE_CHANNELS
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

    Runs on the bot's global fetch job (same interval for all users; see PREFETCH_INTERVAL_MINUTES).

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
    inserted_ids: List[int] = []
    with SessionLocal() as session:
        seen_batch: set[str] = set()
        for item in items:
            is_telegram = (item.source or "").lower().startswith("telegram:")
            # RSS: keep global Sarawak_Local_Keywords gate. Telegram: store all channel posts to DB;
            # per-user delivery uses /settings Area Keywords (see services.get_latest_news_text_for_user).
            if not is_telegram and not matches_local_interest(item.title, item.summary):
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
                session.flush()
                new_id = article.id
                session.commit()
                inserted += 1
                if new_id is not None:
                    inserted_ids.append(int(new_id))
            except IntegrityError:
                session.rollback()  # duplicate link
            except Exception:
                session.rollback()
                raise

    if PREFETCH_AI_SUMMARY and inserted_ids:
        backfill_ai_summaries_for_article_ids(inserted_ids)

    return inserted

