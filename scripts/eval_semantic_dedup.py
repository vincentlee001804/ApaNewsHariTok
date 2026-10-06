"""
FYP2 Phase 3 evaluation: semantic (cosine) same-story detection on stored embeddings.

Two parts:

A. Threshold evidence — labeled pair sets from the production corpus:
     - "jaccard_caught": pairs the current Jaccard pass WOULD flag (expect high cosine)
     - "hard_positive":  same location, published <=48h apart, Jaccard does NOT flag
                         (heuristic stand-in for "same incident, different wording";
                         expected MIXED — the cosine histogram separates true pairs)
     - "negative":       different location, or same location >7 days apart, not flagged
   Outputs a CSV with jaccard + cosine per pair for threshold selection.

B. Before/after simulation — cluster a recent window of articles with
   _cluster_ranked_articles_cross_source() exactly as production calls it, with the
   semantic pass toggled, and list the merges only semantic dedup makes (for a
   false-merge eyeball check).

Run: python scripts/eval_semantic_dedup.py
"""

from __future__ import annotations

import csv
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

BENCH_DIR = PROJECT_ROOT / "docs" / "fyp2" / "benchmark"

HARD_POS_PER_LOC = 14  # sampled same-location/<=48h pairs per location
JACCARD_CAUGHT_CAP = 40
NEGATIVE_CAP = 60
SIM_WINDOW_DAYS = 14


def _max_jaccard(a, b, svc) -> tuple[float, str]:
    """Highest Jaccard signal between two articles, mirroring the production matcher."""
    tt = svc._jaccard_similarity(
        svc._tokenize_for_story_dedup(a.title),
        svc._tokenize_for_story_dedup(b.title),
    )
    bt = svc._jaccard_similarity(
        svc._tokenize_for_story_dedup(f"{a.raw_summary or ''} {a.ai_summary or ''}"),
        svc._tokenize_for_story_dedup(f"{b.raw_summary or ''} {b.ai_summary or ''}"),
    )
    at = svc._jaccard_similarity(
        svc._tokenize_for_story_dedup(a.ai_summary),
        svc._tokenize_for_story_dedup(b.ai_summary),
    )
    best = max((tt, "title"), (bt, "body"), (at, "ai_summary"))
    return best


def _jaccard_flags(a, b, svc, cfg) -> bool:
    tt, bt, at = (
        svc._jaccard_similarity(
            svc._tokenize_for_story_dedup(a.title),
            svc._tokenize_for_story_dedup(b.title),
        ),
        svc._jaccard_similarity(
            svc._tokenize_for_story_dedup(f"{a.raw_summary or ''} {a.ai_summary or ''}"),
            svc._tokenize_for_story_dedup(f"{b.raw_summary or ''} {b.ai_summary or ''}"),
        ),
        svc._jaccard_similarity(
            svc._tokenize_for_story_dedup(a.ai_summary),
            svc._tokenize_for_story_dedup(b.ai_summary),
        ),
    )
    return (
        tt >= cfg.CROSS_SOURCE_DEDUP_TITLE_JACCARD_THRESHOLD
        or bt >= cfg.CROSS_SOURCE_DEDUP_BODY_JACCARD_THRESHOLD
        or at >= cfg.CROSS_SOURCE_DEDUP_AI_SUMMARY_JACCARD_THRESHOLD
    )


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv()
    from sqlalchemy import select

    import src.core.config as cfg
    import src.core.services as svc
    from src.ai.retriever import cosine_between, fetch_stored_vectors
    from src.core.models import NewsArticle
    from src.storage.database import SessionLocal

    rng = random.Random(42)
    now = datetime.utcnow()

    with SessionLocal() as session:
        recent = list(
            session.execute(
                select(NewsArticle)
                .where(NewsArticle.created_at >= now - timedelta(days=45))
                .order_by(NewsArticle.created_at.desc())
            ).scalars().all()
        )

    vecs = fetch_stored_vectors([a.id for a in recent]) or {}
    embedded = [a for a in recent if a.id in vecs]
    print(f"corpus: {len(recent)} articles in 45d, {len(embedded)} with embeddings")

    pairs: list[tuple[str, object, object]] = []
    seen_pair: set[tuple[int, int]] = set()

    def add(label: str, a, b) -> None:
        key = (min(a.id, b.id), max(a.id, b.id))
        if key in seen_pair or a.id == b.id:
            return
        seen_pair.add(key)
        pairs.append((label, a, b))

    by_loc: dict[str, list] = {}
    for a in embedded:
        loc = (a.location or "").strip().lower()
        if loc:
            by_loc.setdefault(loc, []).append(a)

    # hard positives: same location, <=48h apart, not Jaccard-flagged
    for loc, arts in sorted(by_loc.items()):
        arts.sort(key=lambda a: a.created_at)
        loc_pairs = []
        for i, a in enumerate(arts):
            for b in arts[i + 1 :]:
                if (b.created_at - a.created_at).total_seconds() > 48 * 3600:
                    break
                if a.source == b.source:
                    continue
                if not _jaccard_flags(a, b, svc, cfg):
                    loc_pairs.append((a, b))
        rng.shuffle(loc_pairs)
        for a, b in loc_pairs[:HARD_POS_PER_LOC]:
            add("hard_positive", a, b)

    # jaccard_caught: any embedded pair the current thresholds would flag
    caught = []
    for loc, arts in by_loc.items():
        for i, a in enumerate(arts):
            for b in arts[i + 1 :]:
                if (b.created_at - a.created_at).total_seconds() > 48 * 3600:
                    break
                if _jaccard_flags(a, b, svc, cfg):
                    caught.append((a, b))
    rng.shuffle(caught)
    for a, b in caught[:JACCARD_CAUGHT_CAP]:
        add("jaccard_caught", a, b)

    # negatives: different locations, or same location far apart in time
    negs = []
    locs = sorted(by_loc)
    for _ in range(4000):
        a = rng.choice(embedded)
        if rng.random() < 0.5:
            other_locs = [l for l in locs if l != (a.location or "").lower()]
            b = rng.choice(by_loc[rng.choice(other_locs)])
        else:
            b = rng.choice(embedded)
        if abs((a.created_at - b.created_at).total_seconds()) < 7 * 24 * 3600:
            continue
        if _jaccard_flags(a, b, svc, cfg):
            continue
        negs.append((a, b))
    for a, b in negs[:NEGATIVE_CAP]:
        add("negative", a, b)

    rows = []
    for label, a, b in pairs:
        jac, by = _max_jaccard(a, b, svc)
        cos = cosine_between(vecs.get(a.id), vecs.get(b.id))
        rows.append(
            {
                "label": label,
                "id_a": a.id,
                "id_b": b.id,
                "title_a": (a.title or "")[:90],
                "title_b": (b.title or "")[:90],
                "source_a": a.source,
                "source_b": b.source,
                "location": a.location or "",
                "age_gap_hours": round(
                    abs((a.created_at - b.created_at).total_seconds()) / 3600, 1
                ),
                "jaccard_best": round(jac, 3),
                "jaccard_by": by,
                "cosine": round(cos, 4) if cos is not None else None,
            }
        )

    BENCH_DIR.mkdir(parents=True, exist_ok=True)
    out = BENCH_DIR / "dedup_pair_cosines.csv"
    with out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"pair CSV -> {out}  ({len(rows)} pairs)")

    for label in ("jaccard_caught", "hard_positive", "negative"):
        vals = sorted(r["cosine"] for r in rows if r["label"] == label and r["cosine"] is not None)
        if not vals:
            print(f"{label:<15} n=0")
            continue
        n = len(vals)
        q = lambda p: vals[min(n - 1, int(p * n))]
        print(
            f"{label:<15} n={n:<3} min={vals[0]:.3f} p25={q(0.25):.3f} "
            f"median={q(0.5):.3f} p75={q(0.75):.3f} max={vals[-1]:.3f}"
        )

    # ---- Part B: before/after clustering simulation on a recent window ----
    sim = [a for a in recent if a.created_at >= now - timedelta(days=SIM_WINDOW_DAYS)]
    sim.sort(key=lambda a: a.created_at, reverse=True)
    print(f"\nclustering simulation: {len(sim)} articles from last {SIM_WINDOW_DAYS}d")

    svc.CROSS_SOURCE_DEDUP_SEMANTIC_ENABLED = False
    clusters_before = svc._cluster_ranked_articles_cross_source(sim, max_items=500)
    svc.CROSS_SOURCE_DEDUP_SEMANTIC_ENABLED = True
    clusters_after = svc._cluster_ranked_articles_cross_source(sim, max_items=500)

    merged_before = {}
    for i, (_primary, members) in enumerate(clusters_before):
        for m in members:
            merged_before[m.id] = i
    semantic_only = []
    for i, (primary, members) in enumerate(clusters_after):
        if len(members) < 2:
            continue
        for m in members[1:]:
            if merged_before.get(m.id) != merged_before.get(primary.id):
                cos = cosine_between(vecs.get(m.id), vecs.get(primary.id))
                semantic_only.append((primary, m, cos))

    print(f"clusters before (Jaccard only): {len(clusters_before)}")
    print(f"clusters after  (Jaccard+semantic): {len(clusters_after)}")
    print(f"merges made ONLY by semantic pass: {len(semantic_only)}")
    for primary, m, cos in semantic_only[:30]:
        print(f"  cos={cos:.3f} | {(m.title or '')[:70]}")
        print(f"           -> {(primary.title or '')[:70]}")


if __name__ == "__main__":
    main()
