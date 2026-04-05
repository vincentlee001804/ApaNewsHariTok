from __future__ import annotations

from sqlalchemy import func, select

from src.core.config import FIRST_BOOT_RSS_MAX_PER_FEED
from src.core.models import NewsArticle
from src.storage.database import SessionLocal


def news_db_is_empty() -> bool:
    """True when there are no rows in news_articles (fresh install / cleared DB)."""
    with SessionLocal() as session:
        n = session.execute(select(func.count()).select_from(NewsArticle)).scalar_one()
    return int(n or 0) == 0


def effective_rss_limit_per_feed(requested: int) -> int:
    """
    On first boot, cap how many entries we take per feed so restarts do not pull hundreds
    of items into the pipeline at once.
    """
    req = max(1, int(requested))
    if news_db_is_empty():
        return min(req, max(1, FIRST_BOOT_RSS_MAX_PER_FEED))
    return req
