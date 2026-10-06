from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import delete, select

from src.core.config import DB_RETENTION_DAYS
from src.core.models import NewsArticle, UserArticleDelivery
from src.storage.database import SessionLocal, engine

logger = logging.getLogger(__name__)


def cleanup_old_news_data(retention_days: int | None = None) -> dict[str, int]:
    """
    Delete old news rows and related delivery/embedding rows to control DB growth.
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
            return {"articles_deleted": 0, "deliveries_deleted": 0, "embeddings_deleted": 0}

        deliveries_deleted = session.execute(
            delete(UserArticleDelivery).where(
                UserArticleDelivery.article_id.in_(old_article_ids)
            )
        ).rowcount or 0

        # FYP2: embeddings reference news_articles via FK (no cascade) — delete first or
        # the article delete below would violate the constraint on Postgres.
        embeddings_deleted = 0
        if engine.dialect.name == "postgresql":
            try:
                from src.core.models import ArticleEmbedding

                embeddings_deleted = session.execute(
                    delete(ArticleEmbedding).where(
                        ArticleEmbedding.article_id.in_(old_article_ids)
                    )
                ).rowcount or 0
            except Exception as e:
                logger.warning("[cleanup] embedding cleanup skipped: %s", e)

        articles_deleted = session.execute(
            delete(NewsArticle).where(NewsArticle.id.in_(old_article_ids))
        ).rowcount or 0

        session.commit()
        return {
            "articles_deleted": int(articles_deleted),
            "deliveries_deleted": int(deliveries_deleted),
            "embeddings_deleted": int(embeddings_deleted),
        }

