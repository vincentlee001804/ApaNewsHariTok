"""Fill today's missing article summaries with local Ollama only."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill today's missing AI summaries using localhost Ollama."
    )
    parser.add_argument("--model", default="llama3.1", help="Local Ollama model")
    parser.add_argument("--limit", type=int, default=0, help="Maximum articles; 0 means all")
    args = parser.parse_args()
    if args.limit < 0:
        parser.error("--limit must be 0 or greater")

    load_dotenv(".env")
    os.environ["OLLAMA_API_BASE"] = "http://localhost:11434"
    os.environ["OLLAMA_MODEL"] = args.model
    os.environ["OLLAMA_API_BASE_FALLBACK"] = ""
    os.environ["OLLAMA_API_BASE_FALLBACK_2"] = ""
    os.environ["OLLAMA_MODEL_FALLBACK_2"] = ""

    from datetime import datetime

    from sqlalchemy import select

    from src.core.models import NewsArticle
    from src.core.services import backfill_ai_summaries_for_article_ids
    from src.storage.database import SessionLocal

    start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    missing = (NewsArticle.ai_summary.is_(None)) | (NewsArticle.ai_summary == "")
    with SessionLocal() as session:
        stmt = (
            select(NewsArticle.id)
            .where(NewsArticle.created_at >= start, missing)
            .order_by(NewsArticle.created_at.desc())
        )
        if args.limit:
            stmt = stmt.limit(args.limit)
        ids = list(session.execute(stmt).scalars().all())

    print(f"Backfilling {len(ids)} article(s) with local {args.model}; cloud fallback disabled.")
    for offset in range(0, len(ids), 5):
        batch = ids[offset : offset + 5]
        backfill_ai_summaries_for_article_ids(batch)
        print(f"Processed {min(offset + len(batch), len(ids))}/{len(ids)}", flush=True)

    with SessionLocal() as session:
        remaining = len(
            list(
                session.execute(
                    select(NewsArticle.id).where(NewsArticle.created_at >= start, missing)
                ).scalars().all()
            )
        )
    print(f"Finished. Articles from today still without summaries: {remaining}.")


if __name__ == "__main__":
    main()
