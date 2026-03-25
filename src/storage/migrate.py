"""
Simple migration script to add missing columns to user_preferences table.
Run this once to update your existing database.
"""
from __future__ import annotations

from sqlalchemy import select, text

from src.storage.database import engine, SessionLocal


def migrate_add_locations_column() -> None:
    """
    Add the 'locations' column to user_preferences table if it doesn't exist.
    """
    try:
        with SessionLocal() as session:
            # Check if table exists first
            result = session.execute(
                text("SELECT name FROM sqlite_master WHERE type='table' AND name='user_preferences'")
            ).fetchone()
            
            if not result:
                # Table doesn't exist yet, will be created by Base.metadata.create_all
                return
            
            # Check if column exists
            result = session.execute(
                text("PRAGMA table_info(user_preferences)")
            ).fetchall()
            
            column_names = [row[1] for row in result]
            
            if "locations" not in column_names:
                print("Adding 'locations' column to user_preferences table...")
                session.execute(
                    text("ALTER TABLE user_preferences ADD COLUMN locations VARCHAR(500) DEFAULT ''")
                )
                session.commit()
                print("✓ Migration completed successfully!")
            # else: column already exists, no action needed
                
    except Exception as e:
        # Silently fail - migration will be handled by table recreation if needed
        pass


def migrate_add_area_keywords_column() -> None:
    """
    Add the 'area_keywords' column to user_preferences table if it doesn't exist.
    """
    try:
        with SessionLocal() as session:
            # Check if table exists first
            result = session.execute(
                text("SELECT name FROM sqlite_master WHERE type='table' AND name='user_preferences'")
            ).fetchone()

            if not result:
                return

            result = session.execute(text("PRAGMA table_info(user_preferences)")).fetchall()
            column_names = [row[1] for row in result]

            if "area_keywords" not in column_names:
                print("Adding 'area_keywords' column to user_preferences table...")
                session.execute(
                    text("ALTER TABLE user_preferences ADD COLUMN area_keywords VARCHAR(1000) DEFAULT ''")
                )
                session.commit()
                print("✓ Migration completed successfully!")
    except Exception:
        pass


def migrate_add_ai_summary_column() -> None:
    """
    Add the 'ai_summary' column to news_articles table if it doesn't exist.
    """
    try:
        with SessionLocal() as session:
            result = session.execute(
                text("SELECT name FROM sqlite_master WHERE type='table' AND name='news_articles'")
            ).fetchone()

            if not result:
                return

            result = session.execute(text("PRAGMA table_info(news_articles)")).fetchall()
            column_names = [row[1] for row in result]

            if "ai_summary" not in column_names:
                print("Adding 'ai_summary' column to news_articles table...")
                session.execute(
                    text("ALTER TABLE news_articles ADD COLUMN ai_summary TEXT")
                )
                session.commit()
                print("✓ Migration completed successfully!")
    except Exception:
        pass


def migrate_add_news_article_location_and_state_columns() -> None:
    """
    Add `location` + `state` columns to `news_articles` if they don't exist.
    """
    try:
        with SessionLocal() as session:
            result = session.execute(
                text(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='news_articles'"
                )
            ).fetchone()

            if not result:
                return

            result = session.execute(text("PRAGMA table_info(news_articles)")).fetchall()
            column_names = [row[1] for row in result]

            if "location" not in column_names:
                print("Adding 'location' column to news_articles table...")
                session.execute(
                    text("ALTER TABLE news_articles ADD COLUMN location VARCHAR(255)")
                )
                session.commit()

            if "state" not in column_names:
                print("Adding 'state' column to news_articles table...")
                session.execute(
                    text("ALTER TABLE news_articles ADD COLUMN state VARCHAR(20)")
                )
                session.commit()
    except Exception:
        pass


def backfill_news_article_location_and_state() -> None:
    """
    Backfill `location` + `state` for existing rows (best-effort).
    """
    try:
        from src.core.location_extractor import extract_location_and_state
        from src.core.models import NewsArticle

        with SessionLocal() as session:
            rows = session.execute(
                select(NewsArticle).where(NewsArticle.state.is_(None))
            ).scalars().all()

            if not rows:
                return

            for art in rows:
                loc, state = extract_location_and_state(
                    art.title or "",
                    art.raw_summary,
                )
                art.location = loc
                art.state = state

            session.commit()
    except Exception:
        # Best-effort only.
        pass


if __name__ == "__main__":
    migrate_add_locations_column()
    migrate_add_area_keywords_column()
    migrate_add_ai_summary_column()
    migrate_add_news_article_location_and_state_columns()
    backfill_news_article_location_and_state()