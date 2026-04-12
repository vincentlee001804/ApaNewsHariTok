from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import select

from src.core.models import User, UserPreference
from src.storage.database import SessionLocal


def get_or_create_user(telegram_id: int, username: Optional[str] = None) -> User:
    """
    Get an existing user by telegram_id, or create a new one if not found.
    Returns the User instance.
    """
    with SessionLocal() as session:
        user: User | None = session.execute(
            select(User).where(User.telegram_id == telegram_id)
        ).scalar_one_or_none()

        if not user:
            user = User(
                telegram_id=telegram_id,
                username=username,
                first_seen_at=datetime.utcnow(),
                is_active=True,
            )
            session.add(user)
            session.flush()  # Get the user.id

            # Create default preferences
            preference = UserPreference(
                user_id=user.id,
                categories="",  # Empty means all categories
                locations="",  # Empty means all Sarawak locations
                frequency="every_1h",
                wants_urgent_alerts=True,
            )
            session.add(preference)
            session.commit()
        else:
            # Update username if provided and different
            if username and user.username != username:
                user.username = username
                session.commit()

        return user


def get_user_preference(telegram_id: int) -> Optional[UserPreference]:
    """
    Get the UserPreference for a given telegram_id, or None if user doesn't exist.
    """
    with SessionLocal() as session:
        user: User | None = session.execute(
            select(User).where(User.telegram_id == telegram_id)
        ).scalar_one_or_none()

        if not user:
            return None

        preference: UserPreference | None = session.execute(
            select(UserPreference).where(UserPreference.user_id == user.id)
        ).scalar_one_or_none()

        return preference


def update_user_preference(
    telegram_id: int,
    categories: Optional[str] = None,
    locations: Optional[str] = None,
    area_keywords: Optional[str] = None,
    frequency: Optional[str] = None,
    wants_urgent_alerts: Optional[bool] = None,
) -> Optional[UserPreference]:
    """
    Update user preferences. Returns the updated UserPreference or None if user doesn't exist.
    """
    with SessionLocal() as session:
        user: User | None = session.execute(
            select(User).where(User.telegram_id == telegram_id)
        ).scalar_one_or_none()

        if not user:
            return None

        preference: UserPreference | None = session.execute(
            select(UserPreference).where(UserPreference.user_id == user.id)
        ).scalar_one_or_none()

        if not preference:
            # Create default preference if it doesn't exist
            preference = UserPreference(
                user_id=user.id,
                categories="",
                locations="",
                frequency="every_1h",
                wants_urgent_alerts=True,
            )
            session.add(preference)

        if categories is not None:
            preference.categories = categories
        if locations is not None:
            preference.locations = locations
        if area_keywords is not None:
            preference.area_keywords = area_keywords
        if frequency is not None:
            preference.frequency = frequency
        if wants_urgent_alerts is not None:
            preference.wants_urgent_alerts = wants_urgent_alerts

        preference.updated_at = datetime.utcnow()
        session.commit()
        session.refresh(preference)
        return preference


def touch_last_scheduled_push_at(telegram_id: int, at: datetime | None = None) -> None:
    """
    Record that a scheduled push was delivered to this user (after successful Telegram send).
    """
    when = at or datetime.utcnow()
    with SessionLocal() as session:
        user: User | None = session.execute(
            select(User).where(User.telegram_id == telegram_id)
        ).scalar_one_or_none()
        if not user:
            return
        pref: UserPreference | None = session.execute(
            select(UserPreference).where(UserPreference.user_id == user.id)
        ).scalar_one_or_none()
        if not pref:
            return
        pref.last_scheduled_push_at = when
        pref.updated_at = when
        session.commit()


def list_active_user_preferences() -> list[tuple[int, UserPreference, datetime]]:
    """
    Return active users with their preference records as:
    [(telegram_id, preference, first_seen_at), ...]

    first_seen_at is used by the scheduled push job for a post-start grace period.
    """
    with SessionLocal() as session:
        rows = session.execute(
            select(User.telegram_id, UserPreference, User.first_seen_at)
            .join(UserPreference, UserPreference.user_id == User.id)
            .where(User.is_active.is_(True))
        ).all()
        return [(int(telegram_id), pref, first_seen_at) for telegram_id, pref, first_seen_at in rows]


def set_user_active(telegram_id: int, is_active: bool) -> bool:
    """
    Update User.is_active by telegram_id.
    Returns True when user exists and was updated, else False.
    """
    with SessionLocal() as session:
        user: User | None = session.execute(
            select(User).where(User.telegram_id == telegram_id)
        ).scalar_one_or_none()
        if not user:
            return False
        user.is_active = bool(is_active)
        session.commit()
        return True


def is_user_active(telegram_id: int) -> bool:
    """
    Return current User.is_active state.
    Defaults to True for unknown users.
    """
    with SessionLocal() as session:
        user: User | None = session.execute(
            select(User).where(User.telegram_id == telegram_id)
        ).scalar_one_or_none()
        if not user:
            return True
        return bool(user.is_active)
