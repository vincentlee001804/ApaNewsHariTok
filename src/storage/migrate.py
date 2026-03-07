"""
Simple migration script to add the 'locations' column to user_preferences table.
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


if __name__ == "__main__":
    migrate_add_locations_column()
