"""
FYP2 (utility disruption alerts): fetchers + rule-based classifier for official
utility notices (water/power), Stage 1 shadow mode.

Sources (scouting record in docs/fyp2/04-utility-alerts.md):
  - telegram: official Sarawak Water regional channel(s) via the existing session reader
  - jbalb:    Rural Water Supply Department .gov.my announcement list (direct scrape)
  - news:     Google News RSS keyword watches (catches FB/X-only agencies via republishing)

Everything degrades gracefully: a failed source returns [] and the poll continues
(AGENTS.md rule #8). Classification is rule-based (en + ms keywords) so the detector
works with Ollama down; LLM enrichment is optional future work.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup

from src.core.config import RSS_FETCH_TIMEOUT_SEC
from src.core.location_extractor import SARAWAK_LOCATION_ALIASES

logger = logging.getLogger(__name__)

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?q={q}&hl=en-MY&gl=MY&ceid=MY:en"

# Disruption vocabulary — English + Bahasa Malaysia (Sarawak notices are often bilingual).
_WATER_KEYWORDS = (
    "water supply interruption",
    "water disruption",
    "water supply disruption",
    "water cut",
    "water interruption",
    "pipe burst",
    "burst pipe",
    "low water pressure",
    "unscheduled water",
    "water outage",
    "gangguan bekalan air",
    "gangguan air",
    "tiada bekalan air",
    "paip pecah",
    "pancuran air terjejas",
    "tekanan air rendah",
    "intervensi air",
)
_POWER_KEYWORDS = (
    "power outage",
    "power interruption",
    "power supply interruption",
    "electricity outage",
    "electricity supply interruption",
    "blackout",
    "supply interruption",
    "gangguan bekalan elektrik",
    "gangguan elektrik",
    "bekalan elektrik terjejas",
)
_RESTORED_KEYWORDS = (
    "restored",
    "fully restored",
    "supply restored",
    "repair work is completed",
    "repair works completed",
    "repair work completed",
    "completed",
    "dipulihkan",
    "bekalan dipulihkan",
    "kerja pembaikan selesai",
    "selesai",
)
_SCHEDULED_KEYWORDS = (
    "scheduled",
    "planned",
    "maintenance",
    "advisory",
    "berjadual",
    "penyelenggaraan",
)
# Require one of these before treating a text as a disruption alert at all.
_DISRUPTION_TRIGGER_KEYWORDS = _WATER_KEYWORDS + _POWER_KEYWORDS

def _kw_re(keywords: tuple[str, ...]) -> "re.Pattern[str]":
    # Word-boundary match so "scheduled" does NOT fire inside "unscheduled".
    return re.compile(
        r"\b(?:"
        + "|".join(re.escape(k) for k in sorted(keywords, key=len, reverse=True))
        + r")\b",
        re.IGNORECASE,
    )


_DISRUPTION_RE = _kw_re(_DISRUPTION_TRIGGER_KEYWORDS)
_WATER_RE = _kw_re(_WATER_KEYWORDS)
_POWER_RE = _kw_re(_POWER_KEYWORDS)
_RESTORED_RE = _kw_re(_RESTORED_KEYWORDS)
_SCHEDULED_RE = _kw_re(_SCHEDULED_KEYWORDS)

# JBALB list page: announcement links we consider utility-related.
_JBALB_NOTICE_RE = re.compile(
    r"(water|air|bekalan|interruption|gangguan|paip|pipe)", re.IGNORECASE
)
_DETAIL_LINK_RE = re.compile(r"announcement_view/\d+", re.IGNORECASE)

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
}


@dataclass
class RawAlert:
    """Unclassified notice from one source."""

    source: str  # telegram | jbalb | news
    source_ref: str  # canonical link (t.me msg link, notice URL, news URL)
    title: str
    text: str
    published: datetime | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class ClassifiedAlert:
    """RawAlert + rule-based classification fields."""

    raw: RawAlert
    utility_type: str  # water | power | other
    kind: str  # scheduled | unscheduled | restored | info
    locations: list[str]  # canonical Sarawak location keys
    area_text: str
    window_start: datetime | None = None
    window_end: datetime | None = None

    @property
    def is_disruption(self) -> bool:
        return self.kind in {"scheduled", "unscheduled", "restored"}


def classify_utility_alert(title: str, text: str) -> ClassifiedAlert | None:
    """
    Rule-based classification. Returns None when the text is not a disruption notice.
    kind: 'restored' wins over 'scheduled' over 'unscheduled' (a restoration update is
    good news about a past disruption — still worth notifying, kind drives the wording).
    """
    blob = f"{title or ''}\n{text or ''}"
    if not _DISRUPTION_RE.search(blob):
        return None

    utility_type = "water" if _WATER_RE.search(blob) else (
        "power" if _POWER_RE.search(blob) else "other"
    )
    restored = bool(_RESTORED_RE.search(blob))
    scheduled = bool(_SCHEDULED_RE.search(blob))
    if restored:
        kind = "restored"
    elif scheduled:
        kind = "scheduled"
    else:
        kind = "unscheduled"

    locations, area_text = extract_alert_locations(blob)
    return ClassifiedAlert(
        raw=RawAlert(source="", source_ref="", title=title or "", text=text or ""),
        utility_type=utility_type,
        kind=kind,
        locations=locations,
        area_text=area_text,
    )


def extract_alert_locations(text: str) -> tuple[list[str], str]:
    """
    Canonical Sarawak locations mentioned anywhere in the notice + the raw affected-areas
    line for display. Reuses the shared alias map (single taxonomy, AGENTS.md rule #4).
    """
    low = (text or "").lower()
    found: list[str] = []
    for canonical, aliases in SARAWAK_LOCATION_ALIASES.items():
        for alias in aliases:
            if re.search(rf"\b{re.escape(alias)}\b", low):
                found.append(canonical)
                break
    area_text = ""
    m = re.search(
        r"affected\s*areas?\s*[:：](.{5,400})", text or "", re.IGNORECASE | re.DOTALL
    )
    if m:
        area_text = " ".join(m.group(1).split())[:400]
    return found, area_text


# ---------------------------------------------------------------------------
# Fetchers
# ---------------------------------------------------------------------------


def _get(url: str, timeout: int = RSS_FETCH_TIMEOUT_SEC) -> requests.Response | None:
    try:
        r = requests.get(url, headers=_BROWSER_HEADERS, timeout=timeout)
        if r.status_code != 200:
            logger.warning("[utility-alert] GET %s -> HTTP %s", url, r.status_code)
            return None
        return r
    except requests.RequestException as e:
        logger.warning("[utility-alert] GET %s failed: %s", url, e)
        return None


def fetch_jbalb_notices(
    list_url: str, *, max_notices: int = 8, max_detail_fetches: int = 8
) -> list[RawAlert]:
    """
    Scrape the JBALB announcement list page for utility interruption notices, then fetch
    each detail page. Polite: one list request + at most ``max_detail_fetches`` detail
    requests per poll.
    """
    r = _get(list_url)
    if r is None:
        return []
    soup = BeautifulSoup(r.text, "html.parser")

    links: list[tuple[str, str]] = []  # (url, link_text)
    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = " ".join(a.get_text(" ", strip=True).split())
        if not _DETAIL_LINK_RE.search(href):
            continue
        if not _JBALB_NOTICE_RE.search(text):
            continue
        if href.startswith("/"):
            from urllib.parse import urljoin

            href = urljoin(list_url, href)
        links.append((href, text))
        if len(links) >= max_detail_fetches:
            break
    if not links:
        logger.info("[utility-alert] jbalb: no utility notice links found on list page")
        return []

    out: list[RawAlert] = []
    for i, (url, link_text) in enumerate(links[:max_notices]):
        if i:
            time.sleep(1.5)  # be polite to the .gov.my server; the poll runs every 10 min
        d = _get(url)
        if d is None:
            continue
        d_soup = BeautifulSoup(d.text, "html.parser")
        for tag in d_soup(["script", "style", "nav", "header", "footer"]):
            tag.decompose()
        body = " ".join(d_soup.get_text(" ", strip=True).split())
        # The notice body itself loads via AJAX; the static page is site boilerplate
        # ("Announcement - Official Website ... AJAX Error ..."). Strip it so stored
        # text and dedup tokens carry the notice, not the template.
        body = body.split("AJAX Error")[0]
        body = body.replace(
            "Announcement - Official Website of Sarawak Rural Water Supply Department (JBALB)",
            "",
        ).strip()
        body = body or link_text
        title = link_text[:300] or body[:120]
        posted = _parse_posted_date(body)
        out.append(
            RawAlert(
                source="jbalb",
                source_ref=url,
                title=title,
                text=body[:4000],
                published=posted,
            )
        )
    return out


_DATE_PATTERNS = (
    re.compile(r"posted\s+on\s+(\d{1,2}\s+\w+\s+\d{4})", re.IGNORECASE),
    re.compile(r"\b(\d{1,2})[./-](\d{1,2})[./-](\d{4})\b"),
)


def _parse_posted_date(text: str) -> datetime | None:
    m = _DATE_PATTERNS[0].search(text)
    if m:
        for fmt in ("%d %B %Y", "%d %b %Y"):
            try:
                return datetime.strptime(m.group(1), fmt)
            except ValueError:
                continue
    m = _DATE_PATTERNS[1].search(text)
    if m:
        try:
            return datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            return None
    return None


def fetch_news_watch_alerts(
    queries: list[str], *, max_age_hours: int | None = None
) -> list[RawAlert]:
    """
    Google News RSS keyword watches; returns raw items (classification happens later).
    ``max_age_hours`` drops stale items — a months-old outage story is not an alert.
    """
    import feedparser

    out: list[RawAlert] = []
    # Google News' plain q= ranks by relevance (months-old top hits), not recency —
    # the when:Xd operator forces the server to return only recent items.
    when = ""
    if max_age_hours:
        when = f" when:{max(1, (max_age_hours + 23) // 24)}d"
    for q in queries:
        url = GOOGLE_NEWS_RSS.format(q=quote_plus(f"({q}){when}"))
        r = _get(url)
        if r is None:
            continue
        parsed = feedparser.parse(r.content)
        for entry in (parsed.entries or [])[:10]:
            title = (getattr(entry, "title", "") or "").strip()
            link = (getattr(entry, "link", "") or "").strip()
            summary = (getattr(entry, "summary", "") or "").strip()
            published = None
            for attr in ("published_parsed", "updated_parsed"):
                t = getattr(entry, attr, None)
                if t:
                    try:
                        published = datetime(*t[:6])
                    except (TypeError, ValueError):
                        published = None
                    break
            if not title or not link:
                continue
            if (
                published is not None
                and max_age_hours is not None
                and (datetime.utcnow() - published).total_seconds() > max_age_hours * 3600
            ):
                continue
            out.append(
                RawAlert(
                    source="news",
                    source_ref=link,
                    title=title,
                    text=summary or title,
                    published=published,
                )
            )
    return out


def fetch_telegram_alerts(channels: list[str], *, max_age_hours: int) -> list[RawAlert]:
    """Official utility channels via the existing session-based reader (reused infra)."""
    from src.scrapers.telegram_reader import fetch_latest_telegram_items

    items = fetch_latest_telegram_items(
        channels,
        limit_per_source=25,
        max_age_hours=max_age_hours,
    )
    out: list[RawAlert] = []
    for it in items:
        pub = it.published
        if pub is not None and pub.tzinfo is not None:
            pub = pub.replace(tzinfo=None)
        out.append(
            RawAlert(
                source="telegram",
                source_ref=it.link or f"telegram:{it.source}:{(it.title or '')[:80]}",
                title=it.title or "Utility notice",
                text=it.summary or it.title or "",
                published=pub,
            )
        )
    return out
