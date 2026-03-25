from __future__ import annotations

import os

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker


DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///mvp.db")

engine = create_engine(
    DATABASE_URL,
    echo=False,
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

Base = declarative_base()


def init_db() -> None:
    """
    Create all database tables.
    Importing models inside this function avoids circular imports.
    """
    from src.core import models  # noqa: F401  # ensure models are registered

    Base.metadata.create_all(bind=engine)
    
    # Run migrations to add missing columns if needed
    try:
        from src.storage.migrate import (
            migrate_add_area_keywords_column,
            migrate_add_ai_summary_column,
            migrate_add_locations_column,
            migrate_add_news_article_location_and_state_columns,
            backfill_news_article_location_and_state,
        )
        migrate_add_locations_column()
        migrate_add_area_keywords_column()
        migrate_add_ai_summary_column()
        migrate_add_news_article_location_and_state_columns()
        backfill_news_article_location_and_state()
    except Exception as e:
        # If migration fails, it's okay - might be first run or column already exists
        print(f"Migration check: {e}")



