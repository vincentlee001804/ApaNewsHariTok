from __future__ import annotations

import os

from dotenv import load_dotenv
from sqlalchemy import create_engine, event
from sqlalchemy.orm import declarative_base, sessionmaker

# Load .env before DATABASE_URL is read (config.py also calls load_dotenv; safe to repeat).
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///mvp.db")

_IS_SQLITE = DATABASE_URL.startswith("sqlite")

engine = create_engine(
    DATABASE_URL,
    echo=False,
    future=True,
    connect_args={"timeout": 30, "check_same_thread": False} if _IS_SQLITE else {},
)

if _IS_SQLITE:
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA synchronous=NORMAL;")
        cursor.execute("PRAGMA foreign_keys=ON;")
        cursor.close()

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
            migrate_add_ai_title_column,
            migrate_add_delivery_schedule_columns,
            migrate_add_locations_column,
            migrate_add_news_article_category_column,
            migrate_add_news_article_location_and_state_columns,
            migrate_create_user_article_delivery_table,
            migrate_add_last_scheduled_push_at_column,
            migrate_users_telegram_id_to_bigint,
            backfill_news_article_category,
            backfill_news_article_location_and_state,
        )
        migrate_users_telegram_id_to_bigint()
        migrate_add_locations_column()
        migrate_add_area_keywords_column()
        migrate_add_ai_summary_column()
        migrate_add_ai_title_column()
        migrate_add_news_article_location_and_state_columns()
        migrate_add_news_article_category_column()
        migrate_add_delivery_schedule_columns()
        migrate_create_user_article_delivery_table()
        migrate_add_last_scheduled_push_at_column()
        backfill_news_article_location_and_state()
        backfill_news_article_category()
    except Exception as e:
        # If migration fails, it's okay - might be first run or column already exists
        print(f"Migration check: {e}")



