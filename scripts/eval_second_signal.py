"""
FYP2 dedup second-signal evaluation: replay the human-labeled tuning set
(dedup_tuning_set_<date>.csv) against the production merge rules, before and after
the gray-zone second signal (doc 03 §3.3).

- OLD: merge iff cosine >= 0.85
- NEW: merge iff cosine >= 0.85 AND (cosine >= 0.95 OR shared distinctive evidence)
  (mirrors services.py _shared_distinctive_evidence; the batch-df here is computed
  over the tuning-set articles as a proxy for the digest batch)

"unsure" pairs are excluded from metrics but listed. Run from repo root:
  python scripts/eval_second_signal.py
"""

from __future__ import annotations

import csv
import glob
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

THRESHOLD = 0.85
CEILING = 0.95
RARE_MAX_DF = 3

TOKEN_RE = re.compile(r"[a-z0-9]+")
ORDINAL_RE = re.compile(r"\d+(?:st|nd|rd|th)$")
YEAR_RE = re.compile(r"(?:19|20)\d{2}$")


def _is_incident_numeric(tok: str) -> bool:
    """Times (8am), day ordinals (22nd) and bare years are same-window generic —
    not incident evidence (mirrors services.py)."""
    if ORDINAL_RE.match(tok) or YEAR_RE.match(tok):
        return False
    if tok.isdigit() and int(tok) < 10:
        return False
    return True

STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "into", "after", "before",
    "their", "they", "them", "were", "was", "are", "has", "have", "had", "will",
    "said", "says", "when", "where", "which", "while", "about", "over", "under",
    "more", "than", "then", "also", "into", "near", "along", "between", "during",
    "sarawak", "kuching", "malaysia", "news", "general",
}


def evidence(text: str) -> tuple[set[str], set[str]]:
    numeric: set[str] = set()
    content: set[str] = set()
    for tok in TOKEN_RE.findall(text.lower()):
        if any(c.isdigit() for c in tok):
            if _is_incident_numeric(tok):
                numeric.add(tok)
        elif len(tok) >= 4 and tok not in STOPWORDS:
            content.add(tok)
    return numeric, content


def shared_evidence(a: tuple, b: tuple, df: dict[str, int], rare_cap: int) -> bool:
    if a[0] & b[0]:
        return True
    return any(df.get(t, 0) <= rare_cap for t in (a[1] & b[1]))


def main() -> None:
    csv_path = sorted(glob.glob(str(PROJECT_ROOT / "docs/fyp2/benchmark/dedup_tuning_set_*.csv")))[-1]
    with open(csv_path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))

    # document frequency over all article sides in the set (batch proxy)
    df: dict[str, int] = {}
    ev = {}
    for r in rows:
        for side in ("a", "b"):
            e = evidence(f"{r[f'title_{side}']} {r[f'summary_{side}']}")
            ev[(r["pair_id"], side)] = e
            for tok in e[1]:
                df[tok] = df.get(tok, 0) + 1

    def decide(cos: float, pair, rare_cap: int) -> tuple[bool, str]:
        if cos < THRESHOLD:
            return False, "below threshold"
        if cos >= CEILING:
            return True, "cosine >= ceiling"
        ok = shared_evidence(ev[(pair["pair_id"], "a")], ev[(pair["pair_id"], "b")], df, rare_cap)
        return ok, "second signal" if ok else "gray zone, no evidence"

    gold_rows = [r for r in rows if r["label"] in ("same_story", "different_story")]
    unsure = [r for r in rows if r["label"] == "unsure"]

    print(f"pairs: {len(rows)} labeled, {len(unsure)} unsure (excluded from metrics)")
    print()

    def run_rule(name, rare_cap):
        tp = fp = fn = tn = 0
        flips = []
        for r in gold_rows:
            cos = float(r["cosine"])
            merge, why = decide(cos, r, rare_cap)
            gold = r["label"] == "same_story"
            if merge and gold:
                tp += 1
            elif merge and not gold:
                fp += 1
                flips.append((r["pair_id"], cos, "FALSE MERGE", why))
            elif not merge and gold:
                fn += 1
                flips.append((r["pair_id"], cos, "MISSED MERGE", why))
            else:
                tn += 1
        prec = tp / (tp + fp) if tp + fp else 0
        rec = tp / (tp + fn) if tp + fn else 0
        f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0
        print(f"{name}: precision={prec:.3f} recall={rec:.3f} F1={f1:.3f} "
              f"(tp={tp} fp={fp} fn={fn} tn={tn})")
        return flips

    run_rule("OLD cos>=0.85 (no second signal)", None) if False else None
    # baseline without the second signal
    tp = fp = fn = tn = 0
    for r in gold_rows:
        merge = float(r["cosine"]) >= THRESHOLD
        gold = r["label"] == "same_story"
        tp += merge and gold
        fp += merge and not gold
        fn += (not merge) and gold
        tn += (not merge) and not gold
    prec, rec = tp / (tp + fp), tp / (tp + fn)
    print(f"OLD cos>=0.85: precision={prec:.3f} recall={rec:.3f} "
          f"F1={2*prec*rec/(prec+rec):.3f} (tp={tp} fp={fp} fn={fn} tn={tn})\n")

    all_flips = {}
    for cap in (3, 4, 5):
        all_flips[cap] = run_rule(f"NEW + second signal (rare_cap={cap})", cap)
        print()

    print("flips vs OLD for rare_cap=4:")
    for pid, cos, kind, why in all_flips[4]:
        print(f"    {kind} {pid} cos={cos:.4f} ({why})")

    if unsure:
        print("\nunsure pairs under the NEW rule (rare_cap=4):")
        for r in unsure:
            cos = float(r["cosine"])
            merge, why = decide(cos, r, 4)
            print(f"    {r['pair_id']} cos={cos:.4f} -> {'MERGE' if merge else 'no merge'} ({why})")


if __name__ == "__main__":
    main()
