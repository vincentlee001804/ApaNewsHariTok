"""
FYP2 benchmark: summary QUALITY across the four thesis dimensions
(relevance, coherence, factual consistency, compression — thesis Ch. 5.5).

Pairs with benchmark_summaries.py (length consistency). This script joins the
summary CSVs against the article fixture and computes, per produced summary:

  - compression       summary_words / source_words          (dimension: compression)
  - ROUGE-L F1        token-level LCS vs the source text     (dimension: relevance proxy)
  - factuality proxy  fraction of summary content tokens
                      (and numbers) present in the source    (dimension: factual consistency proxy)
  - abstention rate   rows with no final_summary / rows      (coverage behavior)

Proxies, not human judgment: ROUGE-L rewards extractive overlap and says nothing
about faithfulness of paraphrase; the token-containment proxy cannot catch a
correctly-worded but wrong fact assembled from source tokens. Documented as a
limitation in docs/fyp2/07-summary-quality-benchmark.md.

Optionally, cross-model LLM judging (--judge) scores relevance / coherence /
factuality on a 1-5 rubric: gpt-oss judges llama3.1 summaries via Ollama Cloud
and llama3.1 judges gpt-oss summaries via local Ollama, so no model grades its
own output.

Usage:
  python scripts/benchmark_summary_quality.py docs/fyp2/benchmark/llama31_v2.csv \
      docs/fyp2/benchmark/gptoss_v2.csv
  python scripts/benchmark_summary_quality.py ... --judge
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.core.config import SUMMARY_TARGET_WORDS  # noqa: E402
from src.ai.summarizer import word_count  # noqa: E402

BENCH_DIR = PROJECT_ROOT / "docs" / "fyp2" / "benchmark"
FIXTURE_PATH = BENCH_DIR / "articles_fixture.json"

TOKEN_RE = re.compile(r"[a-z0-9']+")

STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "if", "then", "else", "when", "at",
    "by", "for", "with", "about", "against", "between", "into", "through",
    "during", "before", "after", "above", "below", "to", "from", "up", "down",
    "in", "out", "on", "off", "over", "under", "again", "further", "once",
    "here", "there", "all", "any", "both", "each", "few", "more", "most",
    "other", "some", "such", "no", "nor", "not", "only", "own", "same", "so",
    "than", "too", "very", "can", "will", "just", "should", "now", "is", "are",
    "was", "were", "be", "been", "being", "have", "has", "had", "having", "do",
    "does", "did", "doing", "would", "could", "ought", "i", "you", "he", "she",
    "it", "we", "they", "them", "his", "her", "its", "their", "this", "that",
    "these", "those", "am", "of",
}


def tokens(text: str) -> list[str]:
    return TOKEN_RE.findall(text.lower())


def content_tokens(text: str) -> list[str]:
    return [t for t in tokens(text) if t not in STOPWORDS]


def number_tokens(text: str) -> list[str]:
    return [t for t in tokens(text) if any(c.isdigit() for c in t)]


def rouge_l_f1(summary: str, source: str) -> dict:
    """Token-level ROUGE-L (LCS precision / recall / F1) against the source."""
    a, b = tokens(summary), tokens(source)
    if not a or not b:
        return {"precision": 0.0, "recall": 0.0, "f1": 0.0}
    # DP over the shorter table; inputs here are tiny (<= a few hundred tokens).
    prev = [0] * (len(b) + 1)
    for i in range(1, len(a) + 1):
        cur = [0] * (len(b) + 1)
        ai = a[i - 1]
        for j in range(1, len(b) + 1):
            if ai == b[j - 1]:
                cur[j] = prev[j - 1] + 1
            else:
                cur[j] = max(prev[j], cur[j - 1])
        prev = cur
    lcs = prev[len(b)]
    p = lcs / len(a)
    r = lcs / len(b)
    f1 = 2 * p * r / (p + r) if (p + r) else 0.0
    return {"precision": round(p, 4), "recall": round(r, 4), "f1": round(f1, 4)}


def containment(summary: str, source: str) -> dict:
    """Fraction of summary content tokens / numeric tokens present in the source."""
    src_set = set(content_tokens(source))
    s_cont = content_tokens(summary)
    n_cont = number_tokens(summary)
    return {
        "content_token_containment": (
            round(sum(1 for t in s_cont if t in src_set) / len(s_cont), 4) if s_cont else None
        ),
        "numeric_containment": (
            round(sum(1 for t in n_cont if t in src_set) / len(n_cont), 4) if n_cont else None
        ),
    }


def score_pair(article: dict, summary: str) -> dict:
    source = article["text"]
    s_words = word_count(summary)
    src_words = word_count(source)
    out = {
        "summary_words": s_words,
        "source_words": src_words,
        "compression_ratio": round(s_words / src_words, 4) if src_words else None,
    }
    out.update(rouge_l_f1(summary, source))
    out.update(containment(summary, source))
    return out


# --------------------------------------------------------------------------- #
# Optional cross-model LLM judging (1-5 rubric)
# --------------------------------------------------------------------------- #
JUDGE_PROMPT = """You are scoring a news summary against its source article on three dimensions.
Reply with ONLY a JSON object: {{"relevance": <1-5>, "coherence": <1-5>, "factuality": <1-5>}}.

Rubric per dimension:
- relevance (1-5): 1 = mostly off-topic or misses the main event; 3 = main event present but notable side-focus or omissions; 5 = captures exactly who/what/where/outcome.
- coherence (1-5): 1 = broken or fragmented text; 3 = understandable but awkward flow; 5 = fluent, natural, complete sentences.
- factuality (1-5): 1 = contains claims not supported by the source; 3 = supported but with unsupported minor detail; 5 = every claim supported by the source.

Headline: {title}
Source article:
\"\"\"{source}\"\"\"
Summary to score:
\"\"\"{summary}\"\"\""""


def _load_judge_clients():
    import requests

    from src.core import config as cfg
    from src.ai.summarizer import parse_summary_output

    class _Client:
        def __init__(self, base: str, key: str | None, model: str, native: bool):
            self.base, self.key, self.model, self.native = base.rstrip("/"), key, model, native

        def ask(self, prompt: str) -> str | None:
            headers = {"Authorization": f"Bearer {self.key}"} if self.key else {}
            if self.native:
                url = f"{self.base}/api/generate"
                payload = {
                    "model": self.model,
                    "prompt": prompt,
                    "format": "json",
                    "stream": False,
                    "options": {"num_predict": 1024},
                }
            else:
                url = f"{self.base}/v1/chat/completions"
                payload = {
                    "model": self.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": False,
                    "max_tokens": 512,
                }
            try:
                r = requests.post(url, json=payload, headers=headers, timeout=300)
                r.raise_for_status()
                data = r.json()
                if self.native:
                    return (data.get("response") or "").strip() or None
                return (data.get("choices", [{}])[0].get("message", {}) or {}).get("content", "").strip() or None
            except Exception:
                return None

    return cfg, _Client, parse_summary_output


def judge_rows(rows: list[dict], fixture_by_id: dict, *, only_label: str | None = None, offset: int = 0, out_path: Path | None = None) -> None:
    """Cross-judge: gpt-oss (cloud) scores llama summaries; llama3.1 (local) scores gpt-oss."""
    cfg, _Client, parse_summary_output = _load_judge_clients()

    judges = {
        "llama31_v2": _Client(cfg.OLLAMA_API_BASE_FALLBACK, cfg.OLLAMA_FALLBACK_API_KEY, cfg.OLLAMA_MODEL_FALLBACK, native=True),
        "gptoss_v2": _Client("http://localhost:11434", None, "llama3.1:latest", native=True),
    }

    if only_label:
        rows = [r for r in rows if r["label"] == only_label]
    rows = rows[offset:]

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = out_path or (BENCH_DIR / f"judge_scores_{stamp}.csv")
    with out_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=["label", "judge_model", "article_id", "relevance", "coherence", "factuality", "raw"],
        )
        writer.writeheader()
        for row in rows:
            label = row["label"]
            judge = judges.get(label)
            if judge is None:
                continue
            art = fixture_by_id[row["article_id"]]
            prompt = JUDGE_PROMPT.format(title=art["title"], source=art["text"], summary=row["final_summary"])
            raw = judge.ask(prompt)
            scores = {"relevance": None, "coherence": None, "factuality": None}
            if raw:
                try:
                    data = json.loads(raw)
                    for k in scores:
                        v = data.get(k)
                        scores[k] = int(v) if isinstance(v, (int, float)) and 1 <= int(v) <= 5 else None
                except Exception:
                    pass  # leave None; raw kept for audit
            writer.writerow(
                {
                    "label": label,
                    "judge_model": judge.model,
                    "article_id": row["article_id"],
                    **scores,
                    "raw": (raw or "")[:400],
                }
            )
            fh.flush()
            print(f"  judged [{label}] #{row['article_id']}: {scores}")
    print(f"Judge scores -> {out_path}")


# --------------------------------------------------------------------------- #
def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("csvs", nargs="+", type=Path, help="Summary CSVs from benchmark_summaries.py")
    parser.add_argument("--fixture", type=Path, default=FIXTURE_PATH)
    parser.add_argument("--judge", action="store_true", help="Also run cross-model LLM judging (needs cloud + local Ollama up)")
    parser.add_argument("--judge-label", choices=["llama31_v2", "gptoss_v2"], default=None, help="Judge only summaries from this arm")
    parser.add_argument("--judge-offset", type=int, default=0, help="Skip the first N judge rows (resume after an interrupted chunk)")
    args = parser.parse_args()

    fixture = json.loads(args.fixture.read_text(encoding="utf-8"))
    fixture_by_id = {str(a["article_id"]): a for a in fixture}

    rows: list[dict] = []
    for p in args.csvs:
        pp = p if p.is_absolute() else PROJECT_ROOT / p
        with pp.open(newline="", encoding="utf-8") as fh:
            rows.extend(csv.DictReader(fh))

    scored_path = BENCH_DIR / "summary_quality_scores.csv"
    fieldnames = [
        "label", "model", "article_id", "title",
        "summary_words", "source_words", "compression_ratio",
        "rougeL_precision", "rougeL_recall", "rougeL_f1",
        "content_token_containment", "numeric_containment",
    ]
    scored_rows: list[dict] = []
    with scored_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            summary = (row.get("final_summary") or "").strip()
            art = fixture_by_id.get(str(row["article_id"]))
            if not summary or art is None:
                continue
            metrics = score_pair(art, summary)
            out = {
                "label": row["label"],
                "model": row["model"],
                "article_id": row["article_id"],
                "title": row.get("title", "")[:80],
                **{
                    "rougeL_precision": metrics["precision"],
                    "rougeL_recall": metrics["recall"],
                    "rougeL_f1": metrics["f1"],
                    "summary_words": metrics["summary_words"],
                    "source_words": metrics["source_words"],
                    "compression_ratio": metrics["compression_ratio"],
                    "content_token_containment": metrics["content_token_containment"],
                    "numeric_containment": metrics["numeric_containment"],
                },
            }
            writer.writerow(out)
            scored_rows.append(out)
    print(f"Scored rows -> {scored_path}")

    # Aggregate per label
    groups: dict[str, list[dict]] = {}
    for r in scored_rows:
        groups.setdefault(r["label"], []).append(r)
    total_by_label: dict[str, int] = {}
    for row in rows:
        total_by_label[row["label"]] = total_by_label.get(row["label"], 0) + 1

    def nums(g, k):
        return [float(r[k]) for r in g if r[k] not in (None, "")]

    metrics: dict[str, dict] = {}
    for label, g in groups.items():
        metrics[label] = {
            "model": g[0]["model"],
            "summaries_scored": len(g),
            "abstentions": total_by_label.get(label, len(g)) - len(g),
            "abstention_rate": round(1 - len(g) / total_by_label[label], 4) if total_by_label.get(label) else 0,
            "mean_compression_ratio": round(statistics.mean(nums(g, "compression_ratio")), 4),
            "mean_rougeL_f1": round(statistics.mean(nums(g, "rougeL_f1")), 4),
            "mean_rougeL_recall": round(statistics.mean(nums(g, "rougeL_recall")), 4),
            "mean_content_token_containment": round(statistics.mean(nums(g, "content_token_containment")), 4),
            "mean_numeric_containment": (
                round(statistics.mean(nums(g, "numeric_containment")), 4) if nums(g, "numeric_containment") else None
            ),
        }

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    metrics_path = BENCH_DIR / f"quality_metrics_{stamp}.json"
    payload = {
        "generated_utc": stamp,
        "fixture": str(args.fixture),
        "summary_target_words": SUMMARY_TARGET_WORDS,
        "note": "automatic proxies; see docs/fyp2/07-summary-quality-benchmark.md for rubric and limitations",
        "groups": metrics,
    }
    metrics_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(metrics, indent=2))
    print(f"Metrics -> {metrics_path}")

    if args.judge:
        judge_rows(
            [r for r in rows if (r.get("final_summary") or "").strip() and r["label"] in ("llama31_v2", "gptoss_v2")],
            fixture_by_id,
            only_label=args.judge_label,
            offset=args.judge_offset,
        )


if __name__ == "__main__":
    main()
