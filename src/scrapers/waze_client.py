from __future__ import annotations

from typing import Any

import requests

from src.core.config import (
    WAZE_COOKIE,
    WAZE_GEO_RSS_URL,
    WAZE_REQUEST_TIMEOUT_SEC,
)


class WazeGeoRssError(Exception):
    """Raised when the Waze live-map georss endpoint cannot be read."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


# Headers aligned with what the embedded live map sends (see network tab / public write-ups).
_DEFAULT_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.waze.com/live-map/directions",
}


def _request_headers() -> dict[str, str]:
    h = dict(_DEFAULT_HEADERS)
    if WAZE_COOKIE:
        h["Cookie"] = WAZE_COOKIE
    return h


def fetch_waze_georss_json(
    *,
    top: float,
    bottom: float,
    left: float,
    right: float,
    env: str = "row",
    types: str = "alerts",
    url: str | None = None,
    timeout_sec: int | None = None,
) -> dict[str, Any]:
    """
    Call the same JSON endpoint the Waze Live Map uses for alerts/traffic.

    `env` must match the region (e.g. ``row`` for Malaysia / most of Asia).
    """
    endpoint = (url or WAZE_GEO_RSS_URL).strip()
    params = {
        "top": top,
        "bottom": bottom,
        "left": left,
        "right": right,
        "env": env,
        "types": types,
    }
    t = timeout_sec if timeout_sec is not None else WAZE_REQUEST_TIMEOUT_SEC
    try:
        r = requests.get(endpoint, params=params, headers=_request_headers(), timeout=t)
    except requests.RequestException as e:
        raise WazeGeoRssError(f"Waze request failed: {e}") from e

    if r.status_code == 403:
        raise WazeGeoRssError(
            "Waze returned 403 Forbidden. Try again from another network, or set WAZE_COOKIE in "
            "`.env` with the Cookie header value from your browser while logged into waze.com "
            "live map (DevTools → Network → georss request → Request Headers → cookie).",
            status_code=403,
        )
    if r.status_code != 200:
        raise WazeGeoRssError(
            f"Waze HTTP {r.status_code}: {r.text[:200]!r}",
            status_code=r.status_code,
        )

    try:
        data = r.json()
    except ValueError as e:
        raise WazeGeoRssError("Waze response was not valid JSON.") from e

    if not isinstance(data, dict):
        raise WazeGeoRssError("Waze JSON root was not an object.")
    return data


def normalize_waze_alert(alert: dict[str, Any]) -> dict[str, Any]:
    """Pick stable fields for prompts and fallbacks."""
    loc = alert.get("location") or {}
    if not isinstance(loc, dict):
        loc = {}
    return {
        "uuid": alert.get("uuid"),
        "type": alert.get("type"),
        "subtype": alert.get("subtype"),
        "street": alert.get("street"),
        "city": alert.get("city"),
        "country": alert.get("country"),
        "reportDescription": alert.get("reportDescription"),
        "reliability": alert.get("reliability"),
        "nThumbsUp": alert.get("nThumbsUp"),
        "pubMillis": alert.get("pubMillis"),
        "latitude": loc.get("y"),
        "longitude": loc.get("x"),
    }


def list_alerts_in_bbox(
    *,
    top: float,
    bottom: float,
    left: float,
    right: float,
    env: str,
    allowed_types: set[str],
    max_alerts: int,
) -> list[dict[str, Any]]:
    """
    Fetch alerts, newest first, keeping only types in ``allowed_types`` (uppercase strings).
    """
    allowed = {x.upper() for x in allowed_types}
    raw = fetch_waze_georss_json(top=top, bottom=bottom, left=left, right=right, env=env)
    alerts = raw.get("alerts") or []
    if not isinstance(alerts, list):
        return []

    # Newest reports first when timestamp is present
    def _millis(a: dict[str, Any]) -> int:
        m = a.get("pubMillis")
        if isinstance(m, (int, float)):
            return int(m)
        return 0

    alerts_sorted = sorted(alerts, key=_millis, reverse=True)

    out: list[dict[str, Any]] = []
    for a in alerts_sorted:
        if not isinstance(a, dict):
            continue
        t = (a.get("type") or "").strip().upper()
        if allowed and t not in allowed:
            continue
        out.append(normalize_waze_alert(a))
        if len(out) >= max_alerts:
            break
    return out
