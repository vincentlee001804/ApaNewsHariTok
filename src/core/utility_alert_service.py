"""
FYP2 (utility disruption alerts): poll official sources, classify, dedupe, store, and
(deliver — stage 2) notify affected users.

Stage 1 (2026-10-06) runs in SHADOW MODE: detection, classification, dedup and
would-be-recipient computation all run for real and are logged, but nothing is pushed
to users (UTILITY_ALERT_SHADOW_MODE=true). Flip the switch after owner review.

Delivery policy (owner-approved 2026-10-06):
  - alerts are matched to users by location preference (empty preference = statewide);
  - users can opt out via user_preferences.wants_urgent_alerts (/settings toggle, stage 2);
  - the scheduled-push quiet window (12am–6am Asia/Kuching) holds alerts until 06:00;
  - per-user rate limit: UTILITY_ALERT_MAX_PER_HOUR_PER_USER.
"""

from __future__ import annotations

import html
import logging
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from src.core.config import (
    CROSS_SOURCE_DEDUP_TITLE_JACCARD_THRESHOLD,
    DB_RETENTION_DAYS,
    UTILITY_ALERT_JBALB_URLS,
    UTILITY_ALERT_LOOKBACK_HOURS,
    UTILITY_ALERT_MAX_PER_HOUR_PER_USER,
    UTILITY_ALERT_NEWS_QUERIES,
    UTILITY_ALERT_SHADOW_MODE,
    UTILITY_ALERT_TELEGRAM_CHANNELS,
    is_scheduled_push_quiet_hours_now,
)
from src.core.models import (
    User,
    UserPreference,
    UtilityAlert,
    UtilityAlertDelivery,
)
from src.core.services import _jaccard_similarity, _tokenize_for_story_dedup
from src.scrapers.utility_alert_reader import (
    ClassifiedAlert,
    RawAlert,
    classify_utility_alert,
    fetch_jbalb_notices,
    fetch_news_watch_alerts,
    fetch_telegram_alerts,
)
from src.storage.database import SessionLocal

logger = logging.getLogger(__name__)

_KIND_LABEL = {
    "scheduled": "Scheduled disruption",
    "unscheduled": "Unscheduled disruption",
    "restored": "Supply restored",
    "info": "Utility notice",
}


def _classify_raw(raw: RawAlert) -> ClassifiedAlert | None:
    c = classify_utility_alert(raw.title, raw.text)
    if c is None:
        return None
    c.raw = raw
    return c


def _is_known_alert(session, source: str, source_ref: str, title: str, text: str) -> bool:
    """Exact source_ref match, or high title-Jaccard against alerts seen in the last 48h.
    Title-only tokens: body text can be site boilerplate (JBALB pages load bodies via
    AJAX), which would make every notice from one source look identical."""
    if session.execute(
        select(UtilityAlert.id).where(
            UtilityAlert.source == source, UtilityAlert.source_ref == source_ref
        )
    ).scalar_one_or_none():
        return True
    cutoff = datetime.utcnow() - timedelta(hours=48)
    recent = session.execute(
        select(UtilityAlert.title).where(UtilityAlert.created_at >= cutoff)
    ).all()
    if not recent:
        return False
    tokens = _tokenize_for_story_dedup(title)
    if not tokens:
        return False
    for (r_title,) in recent:
        sim = _jaccard_similarity(tokens, _tokenize_for_story_dedup(r_title))
        if sim >= CROSS_SOURCE_DEDUP_TITLE_JACCARD_THRESHOLD:
            return True
    return False


def poll_utility_alerts() -> dict:
    """
    One detection cycle across all configured sources. New alerts are inserted and
    logged; in shadow mode the would-be recipients are computed for visibility only.
    Never raises: source failures are counted and the cycle continues (rule #8).
    """
    summary: dict = {
        "fetched": 0,
        "classified": 0,
        "new": 0,
        "duplicates": 0,
        "rejected": 0,
        "sources": {},
    }
    raw: list[RawAlert] = []

    fetchers = {
        "telegram": lambda: fetch_telegram_alerts(
            UTILITY_ALERT_TELEGRAM_CHANNELS, max_age_hours=UTILITY_ALERT_LOOKBACK_HOURS
        ),
        "jbalb": lambda: [
            a
            for url in UTILITY_ALERT_JBALB_URLS
            for a in fetch_jbalb_notices(url)
        ],
        "news": lambda: fetch_news_watch_alerts(
            # News republishing lags the official post by hours — allow a wider window.
            UTILITY_ALERT_NEWS_QUERIES,
            max_age_hours=max(UTILITY_ALERT_LOOKBACK_HOURS, 48),
        ),
    }
    for source, fn in fetchers.items():
        try:
            items = fn()
        except Exception as e:  # noqa: BLE001 — a dead source must not kill the cycle
            logger.warning("[utility-alert] source %s failed: %s", source, e)
            items = []
        summary["sources"][source] = len(items)
        summary["fetched"] += len(items)
        raw.extend(items)

    with SessionLocal() as session:
        for item in raw:
            c = _classify_raw(item)
            if c is None or not c.is_disruption:
                summary["rejected"] += 1
                continue
            # News-watch hygiene: keyword queries can match out-of-state stories (e.g.
            # Sabah water news). Require a Sarawak signal for that source.
            if item.source == "news" and not (
                "sarawak" in f"{item.title} {item.text}".lower() or c.locations
            ):
                summary["rejected"] += 1
                continue
            summary["classified"] += 1
            if _is_known_alert(session, item.source, item.source_ref, item.title, item.text):
                summary["duplicates"] += 1
                continue
            row = UtilityAlert(
                source=item.source,
                source_ref=item.source_ref[:1000],
                title=item.title[:500],
                text=item.text[:4000],
                utility_type=c.utility_type,
                kind=c.kind,
                locations=",".join(c.locations),
                area_text=c.area_text[:1000],
                announced_at=item.published,
                window_start=c.window_start,
                window_end=c.window_end,
                status="restored" if c.kind == "restored" else "active",
            )
            session.add(row)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                summary["duplicates"] += 1
                continue
            summary["new"] += 1
            would_notify = len(_matching_user_ids(session, row))
            logger.info(
                "[utility-alert]%s NEW %s/%s locs=[%s] notify=%d title=%r",
                "[shadow] " if UTILITY_ALERT_SHADOW_MODE else "",
                row.utility_type,
                row.kind,
                row.locations,
                would_notify,
                row.title[:140],
            )

    _cleanup_old_utility_alerts()
    return summary


# ---------------------------------------------------------------------------
# Delivery (stage 2; computed but not sent while shadow mode is on)
# ---------------------------------------------------------------------------


def _user_location_tokens(preference: UserPreference) -> set[str]:
    return {
        x.strip().lower()
        for x in (preference.locations or "").split(",")
        if x.strip()
    }


def _matching_user_ids(session, alert: UtilityAlert) -> list[int]:
    """Users who should receive this alert (opted in + location match)."""
    rows = session.execute(
        select(User.telegram_id, UserPreference.locations)
        .join(UserPreference, UserPreference.user_id == User.id)
        .where(User.is_active.is_(True), UserPreference.wants_urgent_alerts.is_(True))
    ).all()
    alert_locs = {x.strip().lower() for x in (alert.locations or "").split(",") if x.strip()}
    out: list[int] = []
    for telegram_id, locations in rows:
        user_locs = {x.strip().lower() for x in (locations or "").split(",") if x.strip()}
        if not alert_locs or not user_locs or (alert_locs & user_locs):
            out.append(int(telegram_id))
    return out


def _recent_delivery_counts(session, since: datetime) -> dict[int, int]:
    rows = session.execute(
        select(User.telegram_id, func.count(UtilityAlertDelivery.id))
        .join(User, User.id == UtilityAlertDelivery.user_id)
        .where(UtilityAlertDelivery.sent_at >= since)
        .group_by(User.telegram_id)
    ).all()
    return {int(tid): int(n) for tid, n in rows}


def compute_pending_deliveries() -> list[tuple[int, UtilityAlert]]:
    """
    (alert, recipient) pairs not yet delivered, honoring quiet hours and the per-user
    rate limit. In shadow mode callers must NOT send — this is for logging/preview.
    """
    if is_scheduled_push_quiet_hours_now():
        return []
    cutoff = datetime.utcnow() - timedelta(hours=48)
    with SessionLocal() as session:
        alerts = session.execute(
            select(UtilityAlert)
            .where(UtilityAlert.created_at >= cutoff)
            .order_by(UtilityAlert.created_at.asc())
        ).scalars().all()
        if not alerts:
            return []
        counts = _recent_delivery_counts(
            session, datetime.utcnow() - timedelta(hours=1)
        )
        delivered = {
            (tid, aid)
            for tid, aid in session.execute(
                select(User.telegram_id, UtilityAlertDelivery.utility_alert_id)
                .join(User, User.id == UtilityAlertDelivery.user_id)
            ).all()
        }
        out: list[tuple[int, UtilityAlert]] = []
        for alert in alerts:
            for tid in _matching_user_ids(session, alert):
                if (tid, alert.id) in delivered:
                    continue
                if counts.get(tid, 0) >= UTILITY_ALERT_MAX_PER_HOUR_PER_USER:
                    continue
                counts[tid] = counts.get(tid, 0) + 1
                out.append((tid, alert))
        return out


def format_utility_alert_html(alert: UtilityAlert) -> str:
    kind = _KIND_LABEL.get(alert.kind, "Utility notice")
    utility = alert.utility_type.capitalize() if alert.utility_type != "other" else "Utility"
    icon = "🚨" if alert.kind in {"scheduled", "unscheduled"} else "✅"
    lines = [
        f"{icon} <b>{html.escape(utility)} alert — {html.escape(kind)}</b>",
        html.escape(alert.title or "Utility notice"),
    ]
    area = (alert.area_text or alert.locations or "").strip()
    if area:
        lines.append(f"<b>Affected:</b> {html.escape(area)}")
    if alert.window_start or alert.window_end:
        start = alert.window_start.strftime("%d %b %H:%M") if alert.window_start else "?"
        end = alert.window_end.strftime("%d %b %H:%M") if alert.window_end else "?"
        lines.append(f"<b>Window:</b> {start} – {end}")
    if alert.source_ref:
        lines.append(f'<a href="{html.escape(alert.source_ref, quote=True)}">Official notice</a>')
    return "\n".join(lines)


def record_delivery(telegram_id: int, alert_id: int) -> None:
    with SessionLocal() as session:
        user_id = session.execute(
            select(User.id).where(User.telegram_id == telegram_id)
        ).scalar_one_or_none()
        if user_id is None:
            return
        session.add(
            UtilityAlertDelivery(user_id=user_id, utility_alert_id=alert_id)
        )
        try:
            session.commit()
        except IntegrityError:
            session.rollback()


def get_recent_utility_alerts(hours: int = 48, limit: int = 15) -> list[UtilityAlert]:
    with SessionLocal() as session:
        return session.execute(
            select(UtilityAlert)
            .where(UtilityAlert.created_at >= datetime.utcnow() - timedelta(hours=hours))
            .order_by(UtilityAlert.created_at.desc())
            .limit(limit)
        ).scalars().all()


def preview_text() -> str:
    """Plain-text summary for the developer /devutility preview command."""
    alerts = get_recent_utility_alerts()
    if not alerts:
        return "No utility alerts detected in the last 48h."
    lines = ["<b>[Dev — utility alerts, shadow mode]</b>", ""]
    with SessionLocal() as session:
        for a in alerts:
            notify = len(_matching_user_ids(session, a))
            lines.append(
                f"• <b>{html.escape(a.utility_type)}/{html.escape(a.kind)}</b> "
                f"({html.escape(a.source)}) locs=[{html.escape(a.locations or '-')}] "
                f"would_notify={notify}\n  {html.escape(a.title[:110])}"
            )
    pending = compute_pending_deliveries()
    lines.append("")
    lines.append(f"Pending deliveries right now: {len(pending)}")
    return "\n".join(lines)


def _cleanup_old_utility_alerts() -> None:
    cutoff = datetime.utcnow() - timedelta(days=DB_RETENTION_DAYS)
    try:
        with SessionLocal() as session:
            old_ids = [
                row[0]
                for row in session.execute(
                    select(UtilityAlert.id).where(UtilityAlert.created_at < cutoff)
                ).all()
            ]
            if not old_ids:
                return
            session.execute(
                delete(UtilityAlertDelivery).where(
                    UtilityAlertDelivery.utility_alert_id.in_(old_ids)
                )
            )
            session.execute(
                delete(UtilityAlert).where(UtilityAlert.id.in_(old_ids))
            )
            session.commit()
            logger.info("[utility-alert] cleaned up %d old alert rows", len(old_ids))
    except Exception as e:  # noqa: BLE001 — cleanup must never break the poll
        logger.warning("[utility-alert] cleanup failed: %s", e)
