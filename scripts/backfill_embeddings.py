"""
FYP2 (pgvector RAG): backfill article_embeddings for existing news_articles.

Embeds the SAME text the FYP1 in-memory retriever used (title + summary + category +
location + state), via the production embed path (src/ai/retriever._embed_text), so the
stored vectors match what semantic ranking previously computed on the fly.

Usage:
    python scripts/backfill_embeddings.py             # all missing articles
    python scripts/backfill_embeddings.py --limit 20  # smoke-test batch

Resumable: skips articles that already have a row. When OLLAMA_EMBED_MODEL changes,
delete existing rows first (RAG_EMBEDDING_DIM must match the new model):

    DELETE FROM article_embeddings;  -- then re-run this script
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import select, text  # noqa: E402

from src.core.config import (  # noqa: E402
    OLLAMA_EMBED_MODEL,
    RAG_EMBED_BATCH_SIZE,
)
from src.core.models import ArticleEmbedding, NewsArticle  # noqa: E402
from src.storage.database import engine, SessionLocal  # noqa: E402


def _embedding_text(art: NewsArticle) -> str:
    """Mirror of retriever._article_text_for_embedding (kept in sync intentionally)."""
    title = (art.title or "").strip()
    summary = ((art.ai_summary or "") or (art.raw_summary or "")).strip()
    parts = [p for p in [title, summary, art.category, art.location, art.state] if p]
    return "\n".join(parts)


def articles_missing_embeddings(limit: int | None) -> list[NewsArticle]:
    with SessionLocal() as session:
        rows = session.execute(
            select(NewsArticle).where(
                NewsArticle.id.notin_(
                    select(ArticleEmbedding.article_id).scalar_subquery()
                )
            ).order_by(NewsArticle.created_at.desc())  # newest first: they enter RAG candidate pools
        ).scalars().all()
    if limit:
        rows = rows[:limit]
    return rows


def backfill(limit: int | None, batch_size: int, dry_run: bool) -> None:
    if engine.dialect.name != "postgresql":
        print("article_embeddings is Postgres-only; nothing to do on this database.")
        return

    # Import here so the script still runs (for --counts) when Ollama is down.
    from src.ai.retriever import _embed_text

    pending = articles_missing_embeddings(limit)
    total = len(pending)
    print(f"Articles pending embedding: {total} (model={OLLAMA_EMBED_MODEL}, batch={batch_size})")
    if dry_run:
        for art in pending[:10]:
            print(f"  #{art.id} [{art.source}] {(art.title or '')[:70]}")
        return
    if not total:
        print("Nothing to backfill.")
        return

    done = failed = 0
    from concurrent.futures import ThreadPoolExecutor

    for start in range(0, total, batch_size):
        batch = pending[start : start + batch_size]
        # Embed the batch in parallel — Ollama serves concurrent requests and the CPU
        # has multiple cores; DB rows are committed once per batch afterwards.
        with ThreadPoolExecutor(max_workers=4) as ex:
            vectors = list(ex.map(lambda a: _embed_text(_embedding_text(a)), batch))
        with SessionLocal() as session:
            for art, vec in zip(batch, vectors):
                if not vec:
                    failed += 1
                    print(f"  #{art.id}: embed failed (Ollama down?) — skipping")
                    continue
                session.add(
                    ArticleEmbedding(
                        article_id=art.id,
                        embedding=vec,
                        model=OLLAMA_EMBED_MODEL,
                    )
                )
                done += 1
            session.commit()
        print(f"  progress: {min(start + batch_size, total)}/{total} embedded (failed so far: {failed})")
        time.sleep(0.5)  # be gentle with local Ollama

    print(f"Backfill complete: {done} embedded, {failed} failed.")


def counts() -> None:
    with engine.connect() as conn:
        total_articles = conn.execute(text("SELECT COUNT(*) FROM news_articles")).scalar()
        embedded = conn.execute(text("SELECT COUNT(*) FROM article_embeddings")).scalar()
        by_model = conn.execute(
            text("SELECT model, COUNT(*) FROM article_embeddings GROUP BY model ORDER BY 2 DESC")
        ).all()
    print(f"news_articles: {total_articles} | embedded: {embedded}")
    for model, n in by_model:
        print(f"  {model}: {n}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None, help="Only process N articles")
    parser.add_argument("--batch-size", type=int, default=RAG_EMBED_BATCH_SIZE)
    parser.add_argument("--dry-run", action="store_true", help="List pending articles only")
    parser.add_argument("--counts", action="store_true", help="Show embedding coverage and exit")
    args = parser.parse_args()

    if args.counts:
        counts()
        return
    backfill(args.limit, args.batch_size, args.dry_run)


if __name__ == "__main__":
    main()
