"""
FYP2 evaluation: does the vector path surface relevant articles the FYP1 window cannot?

FYP1 pipeline fact (services.get_news_agent_response_for_user): candidates = articles from
the LAST 24 HOURS only. Questions about slightly older news can never be answered, no matter
how good the ranking is. The FYP2 vector path makes a much larger pool affordable (one
embedding for the query; SQL does the rest), so this script measures:

  for each (question, known-relevant article):
    1. Is the relevant article inside the 24h FYP1 window?      -> FYP1 ceiling
    2. Is it in the vector top-k over the embedded 7-day pool?  -> FYP2 result

Run after backfilling (scripts/backfill_embeddings.py). Outputs a CSV + console table.
"""

from __future__ import annotations

import csv
import sys
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

BENCH_DIR = PROJECT_ROOT / "docs" / "fyp2" / "benchmark"

# (question, relevant article id) — ids verified against production news_articles titles.
CASES = [
    ("Is there any haze or API update in Serian?", 408219),
    ("What is the latest about the Bebuling Airport in Betong?", 421596),
    ("Any news about high electricity bills or SESCO in Sarawak?", 404282),
    ("What do you know about the Sarawak Delta development?", 404271),
    ("Was there a helicopter crash at Long Lellang? What happened?", 404276),
    ("What is Sarawak doing about cloud seeding for the hot weather?", 407993),
    ("Tell me about the arson cases on 24-hour shops in Kuching", 426583),
    ("What is the SEZ and what will it focus on?", 423257),
    ("Any crime news in Kuching recently?", 431791),
    ("Any water supply or treatment news in Sarawak?", 431983),
]

TOP_K = 10
POOL_DAYS = 30  # match DB_RETENTION_DAYS: the whole live corpus is fair game for FYP2


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv()
    from sqlalchemy import select

    from src.ai.retriever import _vector_top_articles
    from src.core.models import NewsArticle
    from src.storage.database import SessionLocal

    now = datetime.utcnow()
    cutoff_24h = now - timedelta(hours=24)
    cutoff_pool = now - timedelta(days=POOL_DAYS)

    with SessionLocal() as session:
        recent24 = set(
            session.execute(
                select(NewsArticle.id).where(NewsArticle.created_at >= cutoff_24h)
            ).scalars().all()
        )
        pool = list(
            session.execute(
                select(NewsArticle)
                .where(NewsArticle.created_at >= cutoff_pool)
                .order_by(NewsArticle.created_at.desc())
            ).scalars().all()
        )
        meta = {a.id: a for a in pool}
        # relevant articles might be older than the pool; fetch them individually
        for _, rid in CASES:
            if rid not in meta:
                art = session.get(NewsArticle, rid)
                if art:
                    meta[rid] = art

    pool_ids = [a.id for a in pool]

    rows = []
    print(f"{'question':<58} {'in 24h?':>7} {'vec hit?':>8}  relevant title")
    for q, rid in CASES:
        ranked = _vector_top_articles(query=q, article_ids=pool_ids, top_k=TOP_K) or []
        in_24h = rid in recent24
        hit = rid in ranked
        title = (meta[rid].title or "")[:60] if rid in meta else "(not in DB)"
        age_h = round((now - meta[rid].created_at).total_seconds() / 3600, 1) if rid in meta else None
        rows.append(
            {
                "question": q,
                "relevant_article_id": rid,
                "relevant_title": title,
                "age_hours": age_h,
                "in_fyp1_24h_window": in_24h,
                "in_fyp2_vector_top10": hit,
                "vector_rank": (ranked.index(rid) + 1) if hit else None,
            }
        )
        print(f"{q[:58]:<58} {str(in_24h):>7} {str(hit):>8}  {title} (age {age_h}h)")

    fyp1_hits = sum(1 for r in rows if r["in_fyp1_24h_window"])
    fyp2_hits = sum(1 for r in rows if r["in_fyp2_vector_top10"])
    print(f"\nRelevant article reachable: FYP1 (24h window) {fyp1_hits}/{len(rows)} | "
          f"FYP2 (vector top-{TOP_K} over {POOL_DAYS}d pool) {fyp2_hits}/{len(rows)}")

    BENCH_DIR.mkdir(parents=True, exist_ok=True)
    out = BENCH_DIR / "rag_pool_coverage.csv"
    with out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"CSV -> {out}")


if __name__ == "__main__":
    main()
