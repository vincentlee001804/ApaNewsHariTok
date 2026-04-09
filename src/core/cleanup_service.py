from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import delete, select

from src.core.config import DB_RETENTION_DAYS
from src.core.models import NewsArticle, UserArticleDelivery
from src.storage.database import SessionLocal


def cleanup_old_news_data(retention_days: int | None = None) -> dict[str, int]:
    """
    Delete old news rows and related delivery rows to control DB growth.
    Returns counts for observability in logs.
    """
    days = int(retention_days or DB_RETENTION_DAYS)
    days = max(1, days)
    cutoff = datetime.utcnow() - timedelta(days=days)

    with SessionLocal() as session:
        old_article_ids = list(
            session.execute(
                select(NewsArticle.id).where(NewsArticle.created_at < cutoff)
            )
            .scalars()
            .all()
        )

        if not old_article_ids:
            return {"articles_deleted": 0, "deliveries_deleted": 0}

        deliveries_deleted = session.execute(
            delete(UserArticleDelivery).where(
                UserArticleDelivery.article_id.in_(old_article_ids)
            )
        ).rowcount or 0

        articles_deleted = session.execute(
            delete(NewsArticle).where(NewsArticle.id.in_(old_article_ids))
        ).rowcount or 0

        session.commit()
        return {
            "articles_deleted": int(articles_deleted),
            "deliveries_deleted": int(deliveries_deleted),
        }

