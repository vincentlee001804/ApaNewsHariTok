"""
FYP2 benchmark: summary-length consistency (thesis priority #4).

Measures how well each candidate LLM meets the 30-word summary target using the SAME
prompt builder and cleanup pipeline as production (src/ai/summarizer.py), so results are
directly citable in the report.

Experiments (see docs/fyp2/01-summary-length-consistency.md):
  A. Production model + FYP1 prompt, no retry   (baseline behavior)
  B. Production model + FYP2 prompt + retry      (treatment)
  C. Candidate models (e.g. local llama3.1) + FYP2 prompt, for model selection

Usage:
  1. Export a reproducible article fixture from the database:
       python scripts/benchmark_summaries.py export --n 50 --seed 42

  2. Run (choose the endpoint via OLLAMA_API_BASE env var; models via --models):
       # OpenAI-compatible endpoint (production fallback_2, mimo-v2.5):
       OLLAMA_API_BASE=https://api.xiaomimimo.com/v1 \\
           python scripts/benchmark_summaries.py run --models mimo-v2.5 \\
           --label mimo_v1 --prompt-version v1 --no-retry --out docs/fyp2/benchmark/mimo_v1.csv

       # Local Ollama (llama3.1), FYP2 prompt with retry:
       python scripts/benchmark_summaries.py run --models llama3.1:latest \\
           --label llama31_v2 --offset 0 --limit 10 --out docs/fyp2/benchmark/llama31_v2.csv

  3. Aggregate:
       python scripts/benchmark_summaries.py summarize docs/fyp2/benchmark/mimo_v1.csv \\
           docs/fyp2/benchmark/mimo_v2.csv

Chunking: --offset/--limit + --out let long runs be split across invocations; rows are
appended to the CSV as they complete, so an interrupted chunk keeps its partial results.

NOTE (limitation, document in report): stored articles carry RSS descriptions
(raw_summary), not the full scraped bodies used in live summarization, so benchmark
inputs are slightly shorter than production inputs.
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.core.config import (  # noqa: E402
    OLLAMA_GENERATE_URL,
    OLLAMA_SUMMARY_NUM_PREDICT,
    SUMMARY_TARGET_WORDS,
    ollama_request_headers,
)
from src.ai.summarizer import (  # noqa: E402
    build_summary_prompt,
    finalize_summary_candidate,
    parse_summary_output,
    word_count,
)

BENCH_DIR = PROJECT_ROOT / "docs" / "fyp2" / "benchmark"
FIXTURE_PATH = BENCH_DIR / "articles_fixture.json"

_OPENAI_COMPATIBLE = "/v1" in OLLAMA_GENERATE_URL.lower() or "openai" in OLLAMA_GENERATE_URL.lower()


# --------------------------------------------------------------------------- #
# Prompt v1 — the exact FYP1 instruction, kept here for the baseline experiment
# --------------------------------------------------------------------------- #
def build_summary_prompt_v1(*, text: str, max_words: int, title: str = "") -> str:
    """FYP1 prompt, preserved verbatim for the baseline (control) benchmark run."""
    import textwrap

    title_line = f'Headline: "{title.strip()}"\n' if title and title.strip() else ""
    return textwrap.dedent(
        f"""
        You are summarizing a local news article from Sarawak, Malaysia.
        Read the full article and output a brief {max_words}-word summary in JSON format:
        one or two tight complete sentences with who, what, where, and the main outcome; skip minor detail if needed.
        {title_line}

        Strict relevance rules:
        - The summary MUST match the provided headline/article only.
        - Do NOT use information from other articles or prior context.
        - If the text does not contain enough matching information for the headline, set "no_summary": true.
        - If a place/location is mentioned in the article or headline, keep that exact place in the summary.
          Do not replace it with another city.

        Return ONLY a single valid JSON object (no markdown fences, no text before or after) matching this exact schema:
        {{
            "summary": "Your 1-2 sentence English summary here ending with a complete period.",
            "no_summary": false
        }}

        Rules for the summary text:
        - Write in clear, natural English using simple everyday words.
        - If the source is in Malay or another language, translate faithfully into English.
        - Avoid jargon, legal wording, and technical terms unless necessary.
        - End with a complete sentence with a period (do not stop mid-thought).
        - Use plain text only inside the summary value: no Markdown, no ** or * for bold/italic, no __underscores__.

        Full Article:
        \"\"\"{text.strip()}\"\"\"
        """
    ).strip()


def make_prompt_builder(version: str):
    if version == "v1":
        return lambda *, text, max_words, title, strict=False: build_summary_prompt_v1(
            text=text, max_words=max_words, title=title
        )
    if version == "v2":
        return build_summary_prompt
    raise ValueError(f"unknown prompt version: {version}")


# --------------------------------------------------------------------------- #
# Fixture export
# --------------------------------------------------------------------------- #
def export_fixture(n: int, seed: int) -> None:
    import random
    from sqlalchemy import select

    from src.core.models import NewsArticle
    from src.storage.database import SessionLocal

    with SessionLocal() as session:
        rows = session.execute(
            select(NewsArticle).where(
                (NewsArticle.raw_summary.isnot(None)) & (NewsArticle.raw_summary != "")
            )
        ).scalars().all()

    by_source: dict[str, list[NewsArticle]] = {}
    for art in rows:
        by_source.setdefault(art.source or "unknown", []).append(art)

    rng = random.Random(seed)
    for arts in by_source.values():
        rng.shuffle(arts)

    fixture = []
    sources = sorted(by_source.keys())
    i = 0
    while len(fixture) < n and any(by_source[s] for s in sources):
        src = sources[i % len(sources)]
        if by_source[src]:
            art = by_source[src].pop(0)
            fixture.append(
                {
                    "article_id": art.id,
                    "title": art.title or "",
                    "source": art.source or "",
                    "category": art.category or "",
                    "created_at": (art.created_at.isoformat() if art.created_at else None),
                    # Benchmark input proxy: RSS description (see module docstring note).
                    "text": art.raw_summary or art.ai_summary or "",
                }
            )
        i += 1

    BENCH_DIR.mkdir(parents=True, exist_ok=True)
    FIXTURE_PATH.write_text(json.dumps(fixture, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Exported {len(fixture)} articles -> {FIXTURE_PATH}")


# --------------------------------------------------------------------------- #
# Single attempt against a specific model
# --------------------------------------------------------------------------- #
def _ollama_generate(model: str, prompt: str, timeout: int = 120) -> tuple[str | None, float]:
    started = time.monotonic()
    try:
        if _OPENAI_COMPATIBLE:
            payload = {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "max_tokens": OLLAMA_SUMMARY_NUM_PREDICT,
            }
        else:
            payload = {
                "model": model,
                "prompt": prompt,
                "format": "json",
                "stream": False,
                "options": {"num_predict": OLLAMA_SUMMARY_NUM_PREDICT},
            }
        resp = requests.post(
            OLLAMA_GENERATE_URL,
            json=payload,
            headers=ollama_request_headers(),
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        if _OPENAI_COMPATIBLE:
            raw = (data.get("choices", [{}])[0].get("message", {}) or {}).get("content", "")
        else:
            raw = data.get("response", "")
        return (raw or "").strip(), time.monotonic() - started
    except Exception:
        return None, time.monotonic() - started


def _attempt(model: str, article: dict, max_words: int, strict: bool, builder) -> dict:
    prompt = builder(
        text=article["text"],
        max_words=max_words,
        title=article["title"],
        strict=strict,
    )
    raw, latency = _ollama_generate(model, prompt)
    record = {
        "strict": strict,
        "latency_s": round(latency, 2),
        "parse_ok": False,
        "no_summary": False,
        "raw_words": None,
        "final_words": None,
        "over_limit": None,
        "summary": "",
    }
    if raw is None:
        return record
    summary, no_summary = parse_summary_output(raw)
    record["no_summary"] = no_summary
    if no_summary or not summary:
        return record
    record["parse_ok"] = True
    record["raw_words"] = word_count(summary)
    final = finalize_summary_candidate(summary, title=article["title"], source_text=article["text"])
    if final:
        record["summary"] = final
        record["final_words"] = word_count(final)
        record["over_limit"] = record["final_words"] > max_words
    return record


FIELDNAMES = [
    "label", "model", "prompt_version", "article_id", "source", "title",
    "first_parse_ok", "first_no_summary", "first_final_words", "first_over_limit",
    "first_latency_s", "retry_run", "retry_final_words", "retry_over_limit",
    "final_words", "final_summary",
]


def _process_article(model, article, max_words, builder, use_retry, label, prompt_version):
    first = _attempt(model, article, max_words, strict=False, builder=builder)
    retry = None
    if use_retry and first["parse_ok"] and first["over_limit"]:
        retry = _attempt(model, article, max_words, strict=True, builder=builder)

    return {
        "label": label,
        "model": model,
        "prompt_version": prompt_version,
        "article_id": article["article_id"],
        "source": article["source"],
        "title": article["title"],
        "first_parse_ok": first["parse_ok"],
        "first_no_summary": first["no_summary"],
        "first_final_words": first["final_words"],
        "first_over_limit": first["over_limit"],
        "first_latency_s": first["latency_s"],
        "retry_run": retry is not None,
        "retry_final_words": retry["final_words"] if retry else None,
        "retry_over_limit": retry["over_limit"] if retry else None,
        "final_words": (
            retry["final_words"]
            if retry and retry["final_words"] and first["final_words"] and retry["final_words"] < first["final_words"]
            else first["final_words"]
        ),
        "final_summary": (retry["summary"] if retry and retry["summary"] else first["summary"]),
    }


def run_benchmark(
    models: list[str],
    max_words: int,
    fixture_path: Path,
    *,
    label: str,
    prompt_version: str,
    use_retry: bool,
    offset: int,
    limit: int | None,
    out: Path,
) -> None:
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    if not fixture:
        print("Fixture is empty. Run the export step first.")
        sys.exit(1)

    slice_ = fixture[offset : (offset + limit) if limit else None]
    if not slice_:
        print(f"offset={offset} beyond fixture size {len(fixture)}")
        sys.exit(1)

    BENCH_DIR.mkdir(parents=True, exist_ok=True)
    out_path = out if out.is_absolute() else PROJECT_ROOT / out
    exists = out_path.exists()
    fh = out_path.open("a", newline="", encoding="utf-8")
    writer = csv.DictWriter(fh, fieldnames=FIELDNAMES)
    if not exists:
        writer.writeheader()

    builder = make_prompt_builder(prompt_version)
    total = len(models) * len(slice_)
    done = 0
    try:
        for model in models:
            print(f"\n=== [{label}] model: {model} | prompt {prompt_version} | retry={use_retry} ===")
            for article in slice_:
                row = _process_article(model, article, max_words, builder, use_retry, label, prompt_version)
                writer.writerow(row)
                fh.flush()  # keep partial results if the chunk is interrupted
                done += 1
                print(
                    f"  [{done}/{total}] #{article['article_id']}: "
                    f"{row['first_final_words']} words"
                    + (f" -> retry {row['retry_final_words']} words" if row["retry_run"] else "")
                )
    finally:
        fh.close()
    print(f"\nAppended {done} rows -> {out_path}")


# --------------------------------------------------------------------------- #
# Aggregate one or more CSVs into metrics
# --------------------------------------------------------------------------- #
def summarize(csv_paths: list[Path], max_words: int) -> None:
    rows: list[dict] = []
    for p in csv_paths:
        pp = p if p.is_absolute() else PROJECT_ROOT / p
        with pp.open(newline="", encoding="utf-8") as fh:
            rows.extend(csv.DictReader(fh))

    def num(v):
        try:
            return float(v) if v not in (None, "", "None") else None
        except ValueError:
            return None

    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(r["label"] or r["model"], []).append(r)

    metrics: dict[str, dict] = {}
    for label, g in groups.items():
        ok = [r for r in g if r["first_parse_ok"] == "True" and r["first_no_summary"] != "True"]
        words = [num(r["final_words"]) for r in ok if num(r["final_words"]) is not None]
        retries = [r for r in ok if r["retry_run"] == "True"]
        retries_fixed = [r for r in retries if num(r["retry_final_words"]) is not None and r["retry_over_limit"] == "False"]
        lat = [num(r["first_latency_s"]) for r in ok if num(r["first_latency_s"]) is not None]
        metrics[label] = {
            "model": g[0]["model"],
            "prompt_version": g[0]["prompt_version"],
            "articles": len(g),
            "parse_success_rate": round(len(ok) / len(g), 4) if g else 0,
            "within_cap_rate": round(sum(1 for w in words if w <= max_words) / len(words), 4) if words else 0,
            "mean_final_words": round(statistics.mean(words), 1) if words else None,
            "median_final_words": statistics.median(words) if words else None,
            "p90_final_words": sorted(words)[max(0, int(len(words) * 0.9) - 1)] if words else None,
            "max_final_words": max(words) if words else None,
            "retry_triggered": len(retries),
            "retry_fixed_within_cap": len(retries_fixed),
            "mean_first_latency_s": round(statistics.mean(lat), 2) if lat else None,
        }

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    metrics_path = BENCH_DIR / f"metrics_{stamp}.json"
    metrics_path.write_text(
        json.dumps({"generated_utc": stamp, "max_words_target": max_words, "groups": metrics}, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(metrics, indent=2))
    print(f"\nMetrics -> {metrics_path}")


# --------------------------------------------------------------------------- #
def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_export = sub.add_parser("export", help="Sample articles from the DB into a JSON fixture")
    p_export.add_argument("--n", type=int, default=50)
    p_export.add_argument("--seed", type=int, default=42)

    p_run = sub.add_parser("run", help="Run the benchmark for one or more models")
    p_run.add_argument("--models", nargs="+", required=True)
    p_run.add_argument("--max-words", type=int, default=SUMMARY_TARGET_WORDS)
    p_run.add_argument("--fixture", type=Path, default=FIXTURE_PATH)
    p_run.add_argument("--label", required=True, help="Campaign label, e.g. mimo_v1 (used in CSV + metrics)")
    p_run.add_argument("--prompt-version", choices=["v1", "v2"], default="v2")
    p_run.add_argument("--no-retry", action="store_true", help="Disable the strict retry (simulates FYP1)")
    p_run.add_argument("--offset", type=int, default=0)
    p_run.add_argument("--limit", type=int, default=None)
    p_run.add_argument("--out", type=Path, required=True, help="CSV output path (appends if it exists)")

    p_sum = sub.add_parser("summarize", help="Aggregate CSV(s) into metrics JSON")
    p_sum.add_argument("csvs", nargs="+", type=Path)
    p_sum.add_argument("--max-words", type=int, default=SUMMARY_TARGET_WORDS)

    args = parser.parse_args()
    if args.cmd == "export":
        export_fixture(args.n, args.seed)
    elif args.cmd == "run":
        run_benchmark(
            args.models,
            args.max_words,
            args.fixture,
            label=args.label,
            prompt_version=args.prompt_version,
            use_retry=not args.no_retry,
            offset=args.offset,
            limit=args.limit,
            out=args.out,
        )
    elif args.cmd == "summarize":
        summarize(args.csvs, args.max_words)


if __name__ == "__main__":
    main()
