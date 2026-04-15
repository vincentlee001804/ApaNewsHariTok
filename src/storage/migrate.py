"""
Simple migration script to add missing columns to user_preferences table.
Run this once to update your existing database.
"""
from __future__ import annotations

from sqlalchemy import inspect, select, text

from src.storage.database import engine, SessionLocal


def migrate_users_telegram_id_to_bigint() -> None:
    """
    Postgres: widen users.telegram_id to BIGINT. Telegram IDs can be > 2^31-1.
    SQLite INTEGER is already 64-bit; skip there.
    """
    if engine.dialect.name != "postgresql":
        return
    try:
        insp = inspect(engine)
        if "users" not in insp.get_table_names():
            return
        cols = {c["name"]: c for c in insp.get_columns("users")}
        col = cols.get("telegram_id")
        if not col:
            return
        # Reflected type name is dialect-specific; INTEGER must become BIGINT.
        tname = str(col["type"]).upper()
        if "BIGINT" in tname:
            return
        with SessionLocal() as session:
            session.execute(
                text("ALTER TABLE users ALTER COLUMN telegram_id TYPE BIGINT")
            )
            session.commit()
            print("✓ users.telegram_id widened to BIGINT (Postgres).")
    except Exception:
        pass


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


def migrate_add_ai_title_column() -> None:
    """
    Add the 'ai_title' column to news_articles table if it doesn't exist.
    """
    try:
        insp = inspect(engine)
        if "news_articles" not in insp.get_table_names():
            return
        cols = {c["name"] for c in insp.get_columns("news_articles")}
        if "ai_title" in cols:
            return
        with SessionLocal() as session:
            session.execute(text("ALTER TABLE news_articles ADD COLUMN ai_title VARCHAR(500)"))
            session.commit()
            print("✓ Added 'ai_title' column to news_articles.")
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


def migrate_create_user_article_delivery_table() -> None:
    """
    Create user_article_delivery for per-user article deduplication (multi-user safe).
    """
    try:
        with SessionLocal() as session:
            exists_tbl = session.execute(
                text(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name='user_article_delivery'"
                )
            ).fetchone()
            if exists_tbl:
                return
            session.execute(
                text(
                    """
                    CREATE TABLE user_article_delivery (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER NOT NULL REFERENCES users(id),
                        article_id INTEGER NOT NULL REFERENCES news_articles(id),
                        sent_at DATETIME NOT NULL,
                        UNIQUE(user_id, article_id)
                    )
                    """
                )
            )
            session.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_user_article_delivery_user_id "
                    "ON user_article_delivery (user_id)"
                )
            )
            session.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_user_article_delivery_article_id "
                    "ON user_article_delivery (article_id)"
                )
            )
            session.commit()
            print("✓ Created user_article_delivery table.")
    except Exception:
        pass


def migrate_add_last_scheduled_push_at_column() -> None:
    """
    Persist last scheduled push time so Fly restarts do not blast all users immediately.
    Backfill from max(user_article_delivery.sent_at) per user when still NULL.
    """
    try:
        insp = inspect(engine)
        if "user_preferences" not in insp.get_table_names():
            return
        cols = {c["name"] for c in insp.get_columns("user_preferences")}
        if "last_scheduled_push_at" not in cols:
            with SessionLocal() as session:
                if engine.dialect.name == "postgresql":
                    session.execute(
                        text(
                            "ALTER TABLE user_preferences "
                            "ADD COLUMN last_scheduled_push_at TIMESTAMP NULL"
                        )
                    )
                else:
                    session.execute(
                        text(
                            "ALTER TABLE user_preferences "
                            "ADD COLUMN last_scheduled_push_at DATETIME NULL"
                        )
                    )
                session.commit()
                print("✓ Added last_scheduled_push_at to user_preferences.")
        if "user_article_delivery" not in insp.get_table_names():
            return
        with SessionLocal() as session:
            session.execute(
                text(
                    """
                    UPDATE user_preferences
                    SET last_scheduled_push_at = (
                        SELECT MAX(uad.sent_at)
                        FROM user_article_delivery AS uad
                        WHERE uad.user_id = user_preferences.user_id
                    )
                    WHERE last_scheduled_push_at IS NULL
                    AND EXISTS (
                        SELECT 1 FROM user_article_delivery AS u2
                        WHERE u2.user_id = user_preferences.user_id
                    )
                    """
                )
            )
            session.commit()
    except Exception:
        pass


def migrate_add_news_article_category_column() -> None:
    """
    Add `category` to `news_articles` (SQLite + Postgres) via SQLAlchemy inspect.
    """
    try:
        insp = inspect(engine)
        cols = insp.get_columns("news_articles")
        names = {c["name"] for c in cols}
        if "category" in names:
            return
        with SessionLocal() as session:
            session.execute(text("ALTER TABLE news_articles ADD COLUMN category VARCHAR(64)"))
            session.commit()
            print("✓ Added 'category' column to news_articles.")
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


def backfill_news_article_category() -> None:
    """Fill `category` for rows where it is missing (keyword taxonomy only; no Ollama)."""
    try:
        from src.core.services import _extract_category
        from src.core.models import NewsArticle

        with SessionLocal() as session:
            rows = list(
                session.execute(
                    select(NewsArticle).where(
                        (NewsArticle.category.is_(None)) | (NewsArticle.category == "")
                    )
                ).scalars().all()
            )
            if not rows:
                return
            for art in rows:
                art.category = _extract_category(
                    art.title or "",
                    art.raw_summary or art.ai_summary,
                )
            session.commit()
            print(f"✓ Backfilled category on {len(rows)} news_articles row(s).")
    except Exception:
        pass


def migrate_add_delivery_schedule_columns() -> None:
    """
    Add structured delivery schedule columns to user_preferences.
    Backward compatible with legacy `frequency`.
    """
    try:
        insp = inspect(engine)
        if "user_preferences" not in insp.get_table_names():
            return
        with SessionLocal() as session:
            if engine.dialect.name == "postgresql":
                # Safer for repeated deploys on Postgres.
                session.execute(
                    text(
                        "ALTER TABLE user_preferences "
                        "ADD COLUMN IF NOT EXISTS delivery_mode VARCHAR(20)"
                    )
                )
                session.execute(
                    text(
                        "ALTER TABLE user_preferences "
                        "ADD COLUMN IF NOT EXISTS frequent_interval_minutes INTEGER"
                    )
                )
                session.execute(
                    text(
                        "ALTER TABLE user_preferences "
                        "ADD COLUMN IF NOT EXISTS digest_morning_enabled BOOLEAN"
                    )
                )
                session.execute(
                    text(
                        "ALTER TABLE user_preferences "
                        "ADD COLUMN IF NOT EXISTS digest_evening_enabled BOOLEAN"
                    )
                )
                session.execute(
                    text(
                        "ALTER TABLE user_preferences "
                        "ADD COLUMN IF NOT EXISTS digest_morning_hour INTEGER"
                    )
                )
                session.execute(
                    text(
                        "ALTER TABLE user_preferences "
                        "ADD COLUMN IF NOT EXISTS digest_evening_hour INTEGER"
                    )
                )
                session.execute(
                    text(
                        "ALTER TABLE user_preferences "
                        "ADD COLUMN IF NOT EXISTS delivery_timezone VARCHAR(64)"
                    )
                )
            else:
                cols = {c["name"] for c in insp.get_columns("user_preferences")}
                if "delivery_mode" not in cols:
                    session.execute(
                        text("ALTER TABLE user_preferences ADD COLUMN delivery_mode VARCHAR(20)")
                    )
                if "frequent_interval_minutes" not in cols:
                    session.execute(
                        text("ALTER TABLE user_preferences ADD COLUMN frequent_interval_minutes INTEGER")
                    )
                if "digest_morning_enabled" not in cols:
                    session.execute(
                        text("ALTER TABLE user_preferences ADD COLUMN digest_morning_enabled BOOLEAN")
                    )
                if "digest_evening_enabled" not in cols:
                    session.execute(
                        text("ALTER TABLE user_preferences ADD COLUMN digest_evening_enabled BOOLEAN")
                    )
                if "digest_morning_hour" not in cols:
                    session.execute(
                        text("ALTER TABLE user_preferences ADD COLUMN digest_morning_hour INTEGER")
                    )
                if "digest_evening_hour" not in cols:
                    session.execute(
                        text("ALTER TABLE user_preferences ADD COLUMN digest_evening_hour INTEGER")
                    )
                if "delivery_timezone" not in cols:
                    session.execute(
                        text("ALTER TABLE user_preferences ADD COLUMN delivery_timezone VARCHAR(64)")
                    )

            # Safe defaults for both new and existing rows.
            session.execute(
                text(
                    """
                    UPDATE user_preferences
                    SET
                      delivery_mode = COALESCE(NULLIF(delivery_mode, ''), 'frequent'),
                      frequent_interval_minutes = COALESCE(frequent_interval_minutes, 60),
                      digest_morning_enabled = COALESCE(digest_morning_enabled, false),
                      digest_evening_enabled = COALESCE(digest_evening_enabled, false),
                      digest_morning_hour = COALESCE(digest_morning_hour, 7),
                      digest_evening_hour = COALESCE(digest_evening_hour, 20),
                      delivery_timezone = COALESCE(NULLIF(delivery_timezone, ''), 'Asia/Kuching')
                    """
                )
            )

            # Backfill from existing frequency values.
            session.execute(
                text(
                    """
                    UPDATE user_preferences
                    SET
                      delivery_mode = 'digest',
                      digest_morning_enabled = true,
                      digest_evening_enabled = false
                    WHERE LOWER(COALESCE(frequency, '')) = 'digest_7am'
                    """
                )
            )
            session.execute(
                text(
                    """
                    UPDATE user_preferences
                    SET
                      delivery_mode = 'digest',
                      digest_morning_enabled = false,
                      digest_evening_enabled = true
                    WHERE LOWER(COALESCE(frequency, '')) = 'digest_8pm'
                    """
                )
            )
            session.execute(
                text(
                    """
                    UPDATE user_preferences
                    SET
                      delivery_mode = 'digest',
                      digest_morning_enabled = true,
                      digest_evening_enabled = true
                    WHERE LOWER(COALESCE(frequency, '')) = 'digest_7am_8pm'
                    """
                )
            )
            session.execute(
                text(
                    """
                    UPDATE user_preferences
                    SET
                      delivery_mode = 'frequent',
                      frequent_interval_minutes = CASE LOWER(COALESCE(frequency, ''))
                        WHEN 'every_15m' THEN 15
                        WHEN 'every_30m' THEN 30
                        WHEN 'every_1h' THEN 60
                        WHEN 'every_3h' THEN 180
                        WHEN 'every_6h' THEN 360
                        WHEN 'every_12h' THEN 720
                        WHEN 'instant' THEN 60
                        WHEN 'daily' THEN 720
                        ELSE COALESCE(frequent_interval_minutes, 60)
                      END
                    WHERE LOWER(COALESCE(frequency, '')) NOT IN ('digest_7am', 'digest_8pm', 'digest_7am_8pm')
                    """
                )
            )
            session.commit()
    except Exception as e:
        print(f"Migration warning (delivery schedule columns): {e}")


if __name__ == "__main__":
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