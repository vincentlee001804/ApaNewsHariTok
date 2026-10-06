"""
FYP2 evaluation: RAG retrieval quality, FYP1 path vs FYP2 vector path.

For each test question this runs the REAL production news-agent pipeline
(services.get_news_agent_response_for_user) twice on identical inputs:
  - vector run:  RAG_VECTOR_ENABLED=True  (pgvector top-k)
  - memory run:  RAG_VECTOR_ENABLED=False (FYP1 in-memory cosine)
and records the candidate pool + ranked article ids each path produced.

LLM answer generation is stubbed out by default (--e2e disables the stub for the
first N questions, at local-CPU cost) so retrieval differences are isolated.

Usage:
    python scripts/test_rag_questions.py                 # retrieval-only, built-in questions
    python scripts/test_rag_questions.py --e2e 3         # also generate real answers for 3 questions

Outputs: docs/fyp2/benchmark/rag_test_<stamp>.csv and .json
Scoring: mark the relevant article ids per question in the JSON, then re-run with
--score docs/fyp2/benchmark/rag_test_<stamp>.json to compute hit@3 / hit@6.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

BENCH_DIR = PROJECT_ROOT / "docs" / "fyp2" / "benchmark"

# Test questions matched to topics actually present in recent production news.
DEFAULT_QUESTIONS = [
    "Is there any haze or API update in Serian?",
    "What is the latest about the Bebuling Airport in Betong?",
    "Any news about high electricity bills or SESCO in Sarawak?",
    "What do you know about the Sarawak Delta development?",
    "Was there a helicopter crash at Long Lellang? What happened?",
    "What is Sarawak doing about cloud seeding for the hot weather?",
    "Any crime news in Kuching recently?",
    "What is the SEZ and what will it focus on?",
    "Any water supply problems in Miri or Sibu?",
    "Tell me about the arson cases on 24-hour shops in Kuching",
]


def run_test(questions: list[str], telegram_id: int, e2e: int) -> dict:
    import src.ai.retriever as retriever_mod
    import src.ai.summarizer as summarizer_mod
    from src.core.services import get_news_agent_response_for_user

    original_rank = retriever_mod.semantic_rank_articles
    original_answer = summarizer_mod.answer_news_question
    stub_answer = lambda question, items_text, max_words=90: "[answer generation skipped]"
    summarizer_mod.answer_news_question = stub_answer

    records: list[dict] = []

    def recorder(tag: str):
        def wrapper(*, query, articles, top_k):
            pool = [getattr(a, "id", None) for a in articles]
            t = time.monotonic()
            ranked = original_rank(query=query, articles=articles, top_k=top_k)
            elapsed = time.monotonic() - t
            records.append(
                {
                    "tag": tag,
                    "query": query,
                    "pool_ids": pool,
                    "ranked_ids": [getattr(a, "id", None) for a in ranked],
                    "rank_seconds": round(elapsed, 3),
                }
            )
            return ranked

        return wrapper

    try:
        for qi, q in enumerate(questions):
            use_e2e = qi < e2e
            if use_e2e:
                summarizer_mod.answer_news_question = original_answer
            else:
                summarizer_mod.answer_news_question = stub_answer

            row: dict = {"question": q, "e2e": use_e2e}

            retriever_mod.semantic_rank_articles = recorder("vector")
            retriever_mod.RAG_VECTOR_ENABLED = True
            t = time.monotonic()
            resp_vector = get_news_agent_response_for_user(telegram_id, q)
            row["vector_seconds"] = round(time.monotonic() - t, 2)
            row["vector_response_preview"] = resp_vector[:300]

            retriever_mod.semantic_rank_articles = recorder("memory")
            retriever_mod.RAG_VECTOR_ENABLED = False
            t = time.monotonic()
            resp_memory = get_news_agent_response_for_user(telegram_id, q)
            row["memory_seconds"] = round(time.monotonic() - t, 2)
            row["memory_response_preview"] = resp_memory[:300]

            records.append({"tag": "summary", **row})
            print(f"[{qi + 1}/{len(questions)}] {q}")
            print(f"    vector: {row['vector_seconds']}s | memory: {row['memory_seconds']}s")
    finally:
        retriever_mod.semantic_rank_articles = original_rank
        summarizer_mod.answer_news_question = original_answer
        retriever_mod.RAG_VECTOR_ENABLED = True

    return {"questions": questions, "telegram_id": telegram_id, "records": records}


def save(results: dict, stamp: str) -> tuple[Path, Path]:
    BENCH_DIR.mkdir(parents=True, exist_ok=True)
    json_path = BENCH_DIR / f"rag_test_{stamp}.json"
    csv_path = BENCH_DIR / f"rag_test_{stamp}.csv"

    json_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")

    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["question", "path", "pool_size", "ranked_ids", "rank_seconds", "response_preview"])
        for r in results["records"]:
            if r["tag"] in ("vector", "memory"):
                writer.writerow(
                    [r["query"], r["tag"], len(r["pool_ids"]),
                     " ".join(str(i) for i in r["ranked_ids"]), r["rank_seconds"], ""]
                )
    return json_path, csv_path


def score(json_path: Path) -> None:
    data = json.loads(json_path.read_text(encoding="utf-8"))
    # relevant: {question: [article_id, ...]} — edit the JSON's "relevant" field to add these
    relevant: dict[str, list[int]] = data.get("relevant", {})
    if not relevant:
        print("No 'relevant' map in the JSON yet. Add '\"relevant\": {\"<question>\": [id, ...]}' then re-run --score.")
        return
    print(f"{'question':<55} hit@3(vec/mem) hit@6(vec/mem)")
    for q, ids in relevant.items():
        ids = set(ids)
        per = {}
        for r in data["records"]:
            if r["tag"] in ("vector", "memory") and r["query"] == q:
                ranked = r["ranked_ids"]
                per[r["tag"]] = (
                    any(i in ids for i in ranked[:3]),
                    any(i in ids for i in ranked[:6]),
                )
        v, m = per.get("vector", (None, None)), per.get("memory", (None, None))
        print(f"{q[:55]:<55} {str(v[0])[:1]}/{str(m[0])[:1]}        {str(v[1])[:1]}/{str(m[1])[:1]}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--telegram-id", type=int, default=0,
                        help="Any value works; unknown ids use default preferences")
    parser.add_argument("--questions", type=Path, default=None, help="JSON list of questions")
    parser.add_argument("--e2e", type=int, default=0, help="Generate real LLM answers for the first N questions")
    parser.add_argument("--score", type=Path, default=None, help="Score a previous run JSON instead of running")
    args = parser.parse_args()

    if args.score:
        score(args.score)
        return

    questions = json.loads(args.questions.read_text(encoding="utf-8")) if args.questions else DEFAULT_QUESTIONS
    results = run_test(questions, args.telegram_id, args.e2e)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path, csv_path = save(results, stamp)
    print(f"\nResults: {json_path}\n         {csv_path}")
    print("Next: open the JSON, add a 'relevant' map (question -> article ids), then:")
    print(f"  python scripts/test_rag_questions.py --score {json_path.name}")


if __name__ == "__main__":
    main()
