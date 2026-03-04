from __future__ import annotations

from datetime import datetime
from typing import List

from sqlalchemy import select

from src.ai.summarizer import summarize
from src.core.config import RSS_FEEDS
from src.core.models import NewsArticle
from src.scrapers.rss_reader import RssItem, fetch_latest_items
from src.storage.database import SessionLocal


def _deduplicate_items(items: List[RssItem], max_items: int) -> List[RssItem]:
    seen_links = set()
    unique_items: List[RssItem] = []
    for item in items:
        if item.link in seen_links:
            continue
        seen_links.add(item.link)
        unique_items.append(item)
        if len(unique_items) >= max_items:
            break
    return unique_items


def get_latest_news_text(max_items: int = 3) -> str:
    """
    Fetch latest items from configured RSS feeds and format them
    into a Markdown string suitable for Telegram, including
    a short AI-generated summary for each item where possible.
    """
    items: List[RssItem] = fetch_latest_items(RSS_FEEDS, limit_per_feed=3)
    unique_items = _deduplicate_items(items, max_items=max_items)

    if not unique_items:
        return (
            "I couldn't fetch any news items right now.\n"
            "_This might be a temporary network issue or the sources are unavailable._"
        )

    # Use the database to skip headlines that were already sent before.
    to_display: List[RssItem] = []
    now = datetime.utcnow()

    with SessionLocal() as session:
        for item in unique_items:
            existing: NewsArticle | None = session.execute(
                select(NewsArticle).where(NewsArticle.link == item.link)
            ).scalar_one_or_none()

            if existing and existing.last_sent_at is not None:
                # This article has already been sent at least once; skip it.
                continue

            if not existing:
                existing = NewsArticle(
                    title=item.title,
                    link=item.link,
                    source=item.source,
                    raw_summary=item.summary,
                    last_sent_at=now,
                )
                session.add(existing)
            else:
                existing.last_sent_at = now

            to_display.append(item)

        session.commit()

    if not to_display:
        return (
            "No new headlines since your last request.\n"
            "_You are up to date with the latest news from these sources._"
        )

    lines: List[str] = ["*Latest local news with AI summaries:*"]

    for idx, item in enumerate(to_display, start=1):
        base_line = f"{idx}. [{item.title}]({item.link})"

        # Prefer the feed-provided summary text if present; otherwise we may later
        # fetch the full article body via a scraper. For now, use summary or title.
        source_text = item.summary or item.title
        ai_summary = summarize(source_text, max_words=40)

        if ai_summary:
            lines.append(f"{base_line}\n   - _{ai_summary}_")
        else:
            # Fallback if Ollama is not running or summarization fails.
            lines.append(f"{base_line}\n   - _(No AI summary available right now.)_")

    lines.append(
        "\n_Summaries generated locally by the LLM (no external AI APIs used)._"
    )
    return "\n".join(lines)


