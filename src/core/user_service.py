from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import select

from src.core.models import User, UserPreference
from src.storage.database import SessionLocal


def _apply_delivery_schedule_from_frequency(preference: UserPreference, frequency: str) -> None:
    key = (frequency or "").strip().lower()
    if key == "instant":
        key = "every_1h"
    elif key == "daily":
        key = "every_12h"

    preference.frequency = key or "every_1h"
    preference.digest_morning_hour = preference.digest_morning_hour or 6
    preference.digest_evening_hour = preference.digest_evening_hour or 21
    preference.delivery_timezone = (preference.delivery_timezone or "").strip() or "Asia/Kuching"

    if key == "digest_7am":
        preference.delivery_mode = "digest"
        preference.digest_morning_enabled = True
        preference.digest_evening_enabled = False
        return
    if key == "digest_8pm":
        preference.delivery_mode = "digest"
        preference.digest_morning_enabled = False
        preference.digest_evening_enabled = True
        return
    if key == "digest_7am_8pm":
        preference.delivery_mode = "digest"
        preference.digest_morning_enabled = True
        preference.digest_evening_enabled = True
        return

    interval_map = {
        "every_15m": 15,
        "every_30m": 30,
        "every_1h": 60,
        "every_3h": 180,
        "every_6h": 360,
        "every_12h": 720,
    }
    preference.delivery_mode = "frequent"
    preference.digest_morning_enabled = False
    preference.digest_evening_enabled = False
    preference.frequent_interval_minutes = interval_map.get(key, 60)


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
                frequency="digest_8pm",
                delivery_mode="digest",
                frequent_interval_minutes=60,
                digest_morning_enabled=False,
                digest_evening_enabled=True,
                digest_morning_hour=6,
                digest_evening_hour=21,
                delivery_timezone="Asia/Kuching",
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
    delivery_mode: Optional[str] = None,
    frequent_interval_minutes: Optional[int] = None,
    digest_morning_enabled: Optional[bool] = None,
    digest_evening_enabled: Optional[bool] = None,
    digest_morning_hour: Optional[int] = None,
    digest_evening_hour: Optional[int] = None,
    delivery_timezone: Optional[str] = None,
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
                frequency="digest_8pm",
                delivery_mode="digest",
                frequent_interval_minutes=60,
                digest_morning_enabled=False,
                digest_evening_enabled=True,
                digest_morning_hour=6,
                digest_evening_hour=21,
                delivery_timezone="Asia/Kuching",
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
            _apply_delivery_schedule_from_frequency(preference, frequency)
        if delivery_mode is not None:
            mode = (delivery_mode or "").strip().lower()
            if mode in {"digest", "frequent"}:
                preference.delivery_mode = mode
        if frequent_interval_minutes is not None:
            preference.frequent_interval_minutes = max(15, int(frequent_interval_minutes))
        if digest_morning_enabled is not None:
            preference.digest_morning_enabled = bool(digest_morning_enabled)
        if digest_evening_enabled is not None:
            preference.digest_evening_enabled = bool(digest_evening_enabled)
        if digest_morning_hour is not None:
            preference.digest_morning_hour = max(0, min(23, int(digest_morning_hour)))
        if digest_evening_hour is not None:
            preference.digest_evening_hour = max(0, min(23, int(digest_evening_hour)))
        if delivery_timezone is not None:
            tz = (delivery_timezone or "").strip()
            if tz:
                preference.delivery_timezone = tz
        if wants_urgent_alerts is not None:
            preference.wants_urgent_alerts = wants_urgent_alerts

        # Keep old `frequency` string usable for legacy views/paths.
        if preference.delivery_mode == "digest":
            m_on = bool(preference.digest_morning_enabled)
            e_on = bool(preference.digest_evening_enabled)
            if m_on and e_on:
                preference.frequency = "digest_7am_8pm"
            elif m_on:
                preference.frequency = "digest_7am"
            elif e_on:
                preference.frequency = "digest_8pm"
            else:
                # Avoid empty digest schedule by defaulting to evening.
                preference.digest_evening_enabled = True
                preference.frequency = "digest_8pm"
        elif preference.delivery_mode == "frequent":
            iv = int(preference.frequent_interval_minutes or 60)
            map_back = {
                15: "every_15m",
                30: "every_30m",
                60: "every_1h",
                180: "every_3h",
                360: "every_6h",
                720: "every_12h",
            }
            preference.frequency = map_back.get(iv, "every_1h")

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
