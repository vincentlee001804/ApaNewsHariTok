from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, select

from src.core.models import NewsArticle
from src.storage.database import SessionLocal


def count_recent_summary_activity(window_hours: int = 6) -> tuple[int, int]:
    """Return (new articles, articles with a non-empty AI summary) in the UTC window."""
    cutoff = datetime.utcnow() - timedelta(hours=max(1, int(window_hours)))
    with SessionLocal() as session:
        total = session.execute(
            select(func.count())
            .select_from(NewsArticle)
            .where(NewsArticle.created_at >= cutoff)
        ).scalar_one()
        summarized = session.execute(
            select(func.count())
            .select_from(NewsArticle)
            .where(
                NewsArticle.created_at >= cutoff,
                NewsArticle.ai_summary.is_not(None),
                func.trim(NewsArticle.ai_summary) != "",
            )
        ).scalar_one()
    return int(total), int(summarized)
