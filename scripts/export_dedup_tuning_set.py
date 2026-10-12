"""
FYP2 Phase 3 follow-up: export a HUMAN-LABELED dedup tuning set (doc 03 §5 threat #1:
the 0.85 threshold came from heuristic labels; human annotation was owed later).

Samples candidate cross-source pairs from the production corpus, stratified around the
0.85 cosine decision boundary (pairs near the threshold are the ones that actually
validate or refute it), plus easy positive/negative anchors to catch a tired labeler.

Output: an .xlsx labeling sheet (fill in the `label` column: same_story /
different_story / unsure) + a CSV mirror for diffing. After labeling, the labels feed
back into scripts/eval_semantic_dedup.py-style histograms to confirm/move the threshold.

Run: python scripts/export_dedup_tuning_set.py
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

WINDOW_DAYS = 45
BAND_LO, BAND_HI = 0.65, 0.97      # decision-relevant band around the 0.85 threshold
N_BAND = 30                        # pairs closest to 0.85 within the band
N_ANCHOR_POS = 5                   # cosine >= 0.95, expected same story
N_ANCHOR_NEG = 5                   # cosine <= 0.60, expected different story
SEED = 7


def _summary_of(a) -> str:
    return (a.ai_summary or a.raw_summary or "").strip()


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv()
    from sqlalchemy import select

    import src.core.config as cfg
    import src.core.services as svc
    from src.ai.retriever import cosine_between, fetch_stored_vectors
    from src.core.models import NewsArticle
    from src.storage.database import SessionLocal

    rng = random.Random(SEED)
    now = datetime.utcnow()

    with SessionLocal() as session:
        recent = list(
            session.execute(
                select(NewsArticle)
                .where(NewsArticle.created_at >= now - timedelta(days=WINDOW_DAYS))
                .order_by(NewsArticle.created_at.desc())
            ).scalars().all()
        )

    vecs = fetch_stored_vectors([a.id for a in recent]) or {}
    embedded = [a for a in recent if a.id in vecs]
    print(f"corpus: {len(recent)} articles in {WINDOW_DAYS}d, {len(embedded)} with embeddings")

    # Candidate pairs: cross-source, same location, <=48h apart (mirrors the
    # hard-positive generator in eval_semantic_dedup.py) — the pairs whose label
    # actually decides whether the threshold is right.
    by_loc: dict[str, list] = {}
    for a in embedded:
        loc = (a.location or "").strip().lower()
        if loc and _summary_of(a):
            by_loc.setdefault(loc, []).append(a)

    candidates: list[tuple[float, float, object, object]] = []  # (cos, jac, a, b)
    for arts in by_loc.values():
        arts.sort(key=lambda a: a.created_at)
        for i, a in enumerate(arts):
            for b in arts[i + 1 :]:
                if (b.created_at - a.created_at).total_seconds() > 48 * 3600:
                    break
                if a.source == b.source:
                    continue
                cos = cosine_between(vecs[a.id], vecs[b.id])
                jac = svc._jaccard_similarity(
                    svc._tokenize_for_story_dedup(f"{a.title} {_summary_of(a)}"),
                    svc._tokenize_for_story_dedup(f"{b.title} {_summary_of(b)}"),
                )
                candidates.append((cos, jac, a, b))
    print(f"candidate same-location/<=48h cross-source pairs: {len(candidates)}")

    band = [(abs(c - cfg.CROSS_SOURCE_DEDUP_SEMANTIC_COSINE_THRESHOLD), c, j, a, b)
            for c, j, a, b in candidates if BAND_LO <= c <= BAND_HI]
    band.sort(key=lambda t: t[0])
    picked: list[tuple[str, float, float, object, object]] = [
        ("band", c, j, a, b) for _, c, j, a, b in band[:N_BAND]
    ]

    anchors_pos = sorted((c, j, a, b) for c, j, a, b in candidates if c >= 0.95)
    rng.shuffle(anchors_pos)
    picked += [("anchor_positive", c, j, a, b) for c, j, a, b in anchors_pos[:N_ANCHOR_POS]]

    anchors_neg = sorted((c, j, a, b) for c, j, a, b in candidates if c <= 0.60)
    rng.shuffle(anchors_neg)
    picked += [("anchor_negative", c, j, a, b) for c, j, a, b in anchors_neg[:N_ANCHOR_NEG]]

    BENCH_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.utcnow().strftime("%Y%m%d")
    xlsx_path = BENCH_DIR / f"dedup_tuning_set_{stamp}.xlsx"
    csv_path = BENCH_DIR / f"dedup_tuning_set_{stamp}.csv"

    header = [
        "pair_id", "stratum", "cosine", "jaccard", "threshold_gap",
        "date_a", "source_a", "location", "title_a", "summary_a",
        "date_b", "source_b", "title_b", "summary_b",
        "label", "notes",
    ]

    def row_of(idx: int, stratum: str, cos: float, jac: float, a, b) -> dict:
        return {
            "pair_id": f"P{idx:02d}",
            "stratum": stratum,
            "cosine": round(cos, 4),
            "jaccard": round(jac, 4),
            "threshold_gap": round(cos - cfg.CROSS_SOURCE_DEDUP_SEMANTIC_COSINE_THRESHOLD, 4),
            "date_a": a.created_at.strftime("%Y-%m-%d %H:%M") if a.created_at else "",
            "source_a": a.source or "",
            "location": a.location or "",
            "title_a": a.title or "",
            "summary_a": _summary_of(a),
            "date_b": b.created_at.strftime("%Y-%m-%d %H:%M") if b.created_at else "",
            "source_b": b.source or "",
            "title_b": b.title or "",
            "summary_b": _summary_of(b),
            "label": "",
            "notes": "",
        }

    rows = [row_of(i + 1, s, c, j, a, b) for i, (s, c, j, a, b) in enumerate(picked)]

    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=header)
        w.writeheader()
        w.writerows(rows)

    import openpyxl
    from openpyxl.worksheet.datavalidation import DataValidation

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "labeling"
    ws.append(header)
    for r in rows:
        ws.append([r[h] for h in header])
    dv = DataValidation(
        type="list", formula1='"same_story,different_story,unsure"', allow_blank=True
    )
    ws.add_data_validation(dv)
    dv.add(f"O2:O{len(rows) + 1}")  # column O = label
    widths = {"A": 8, "B": 16, "C": 9, "D": 9, "E": 12, "F": 16, "G": 18, "H": 14,
              "I": 50, "J": 60, "K": 16, "L": 18, "M": 50, "N": 60, "O": 16, "P": 30}
    for col, wdt in widths.items():
        ws.column_dimensions[col].width = wdt
    ws.freeze_panes = "A2"

    guide = wb.create_sheet("how_to_label")
    guide.append(["Dedup tuning set — how to label"])
    guide.append([])
    for line in [
        f"Goal: validate the production same-story threshold (cosine {cfg.CROSS_SOURCE_DEDUP_SEMANTIC_COSINE_THRESHOLD}).",
        "For each pair, read title + summary of BOTH articles and decide:",
        "  same_story      = both report the same incident/event (follow-ups and updates count,",
        "                    even if one is Malay and one English).",
        "  different_story = genuinely different incidents, or only same broad topic/location/genre.",
        "  unsure          = cannot tell from the text given.",
        "",
        f"stratum 'band': cosine {BAND_LO}-{BAND_HI}, sorted closest to the threshold first —",
        "these pairs are the evidence; the anchor rows are sanity checks (expect obvious answers).",
        "Write anything ambiguous in 'notes' (e.g. 'b is a follow-up of a', 'same festival, different years').",
        "",
        "When done, tell Kimi and the labels will be folded back into the threshold evaluation (doc 03).",
    ]:
        guide.append([line])
    guide.column_dimensions["A"].width = 110

    wb.save(xlsx_path)
    print(f"labeled pairs: {len(rows)}  (band={N_BAND}, anchors {N_ANCHOR_POS}+{N_ANCHOR_NEG})")
    print(f"xlsx -> {xlsx_path}")
    print(f"csv  -> {csv_path}")


if __name__ == "__main__":
    main()
