from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String, Text, UniqueConstraint

from src.storage.database import Base


class NewsArticle(Base):
    """
    Stores basic information about a news article.
    Duplicate prevention is based on the unique 'link' field.
    """

    __tablename__ = "news_articles"
    __table_args__ = (
        UniqueConstraint("link", name="uq_news_link"),
    )

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(500), nullable=False)
    link = Column(String(1000), nullable=False)
    source = Column(String(255), nullable=False)
    raw_summary = Column(Text, nullable=True)  # from RSS feed, optional
    last_sent_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

