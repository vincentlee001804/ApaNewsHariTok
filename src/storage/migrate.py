"""
Simple migration script to add missing columns to user_preferences table.
Run this once to update your existing database.
"""
from __future__ import annotations

from sqlalchemy import text

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


if __name__ == "__main__":
    migrate_add_locations_column()
    migrate_add_area_keywords_column()
    migrate_add_ai_summary_column()