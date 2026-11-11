"""
FYP2 benchmark: conversational news-agent QA (conversation memory + news agent).

Two tiers, mirroring the thesis evaluation dimensions for the conversation
features added in FYP2 (docs/fyp2 conversation-memory commits):

Tier 1 — deterministic heuristic tests (no DB, no LLM):
  Feeds crafted histories through is_followup_query / expand_followup_query
  (src/core/conversation_service.py) and asserts follow-up classification and
  query expansion. This is the "did the bot understand the question" axis.
  Cases marked expected_fail=True document known heuristic limitations
  (e.g. Bahasa Malaysia follow-up markers are not covered).

Tier 2 — end-to-end conversations (DB read-only + LLM):
  Replays scripted multi-turn conversations through
  get_news_agent_response_for_user, simulating exactly what handlers.py does:
  history accumulation, shown-article-link exclusion per conversation.
  Responses are saved (CSV + JSON) for manual 1-5 rubric scoring along the
  thesis dimensions (relevance, coherence, factual consistency) plus two
  conversation-specific auto-metrics:
    - no_repeat: fraction of a turn's shown links never shown earlier in the
      conversation (auto pass/fail; 1.0 = perfect novelty)
    - abstention: response explicitly said it found nothing (informational)

Usage:
  python scripts/benchmark_conversation_qa.py                  # tier 1 only
  python scripts/benchmark_conversation_qa.py --tier2          # both tiers
  python scripts/benchmark_conversation_qa.py --tier2-only

Tier 2 needs a warmed database. Locally this uses DATABASE_URL (default
sqlite:///mvp.db, likely stale); for production-backed runs:
  set DATABASE_URL in .env to the Supabase URL first.
All tier 2 DB access is read-only; the retriever only SELECTs stored
embeddings and computes query embeddings over HTTP.

Outputs (docs/fyp2/benchmark/):
  conversation_tier1_<stamp>.json      tier 1 case results + pass rate
  conversation_e2e_<stamp>.csv         per-turn responses for manual scoring
  conversation_e2e_<stamp>.json        run metadata + auto-metrics
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

# Synthetic user id for tier 2. No user row exists for it, so preference
# filters are empty and every ranking decision is the unpersonalized default.
BENCH_TELEGRAM_ID = 900000001


# --------------------------------------------------------------------------- #
# Tier 1: heuristic cases
# --------------------------------------------------------------------------- #
# History shapes reused across cases (role/content/interaction_type).
TECH_KUCHING_HISTORY = [
    {"role": "user", "content": "Any tech news in Kuching?", "interaction_type": ""},
    {
        "role": "assistant",
        "content": "Here are the latest tech stories around Kuching.",
        "interaction_type": "qa",
    },
]
ACCIDENT_SIBU_HISTORY = [
    {"role": "user", "content": "Any accident news in Sibu?", "interaction_type": ""},
    {
        "role": "assistant",
        "content": "Here are recent accident reports in Sibu.",
        "interaction_type": "qa",
    },
]
# Previous user turn is vague (topic extraction yields None); the assistant's
# failed response is the only place the topic survives.
FAILED_FLOOD_HISTORY = [
    {"role": "user", "content": "how about Kuching?", "interaction_type": ""},
    {
        "role": "assistant",
        "content": "I couldn't find flood news in Kuching right now. You could ask about something else.",
        "interaction_type": "qa",
    },
]

TIER1_CASES: list[dict] = [
    {
        "id": "location_swap",
        "category": "follow-up expansion",
        "history": TECH_KUCHING_HISTORY,
        "query": "how about Sibu?",
        "expect_followup": True,
        "expansion_contains": ["tech", "sibu"],
        "note": "topic carried over, location swapped",
    },
    {
        "id": "topic_swap",
        "category": "follow-up expansion",
        "history": ACCIDENT_SIBU_HISTORY,
        "query": "what about floods?",
        "expect_followup": True,
        "expansion_contains": ["flood", "sibu"],
        "note": "location carried over, topic swapped",
    },
    {
        "id": "vague_tell_me_more",
        "category": "follow-up expansion",
        "history": TECH_KUCHING_HISTORY,
        "query": "tell me more",
        "expect_followup": True,
        "expansion_contains": ["more details", "tech"],
        "note": "vague continuation expanded from previous turn",
    },
    {
        "id": "vague_wh_question",
        "category": "follow-up expansion",
        "history": ACCIDENT_SIBU_HISTORY,
        "query": "why did it happen?",
        "expect_followup": True,
        "expansion_contains": ["why", "accident"],
        "note": "wh- follow-up keeps previous topic (aux-verb junk filtered from topic extraction)",
    },
    {
        "id": "greeting_prefix",
        "category": "robustness",
        "history": TECH_KUCHING_HISTORY,
        "query": "Hello, how about Miri?",
        "expect_followup": True,
        "expansion_contains": ["tech", "miri"],
        "note": "leading greeting must not become the topic",
    },
    {
        "id": "punctuation",
        "category": "robustness",
        "history": TECH_KUCHING_HISTORY,
        "query": "how about sibu??",
        "expect_followup": True,
        "expansion_contains": ["tech", "sibu"],
        "note": "punctuation must not break location extraction",
    },
    {
        "id": "failed_response_recovery",
        "category": "follow-up expansion",
        "history": FAILED_FLOOD_HISTORY,
        "query": "how about Miri?",
        "expect_followup": True,
        "expansion_contains": ["flood", "miri"],
        "note": "topic recovered from 'I couldn't find X news' phrasing",
    },
    {
        "id": "single_vague_word",
        "category": "follow-up classification",
        "history": TECH_KUCHING_HISTORY,
        "query": "interesting",
        "expect_followup": True,
        "expansion_contains": ["tech"],
        "note": "single contentless word is a follow-up",
    },
    {
        "id": "long_self_contained",
        "category": "follow-up classification",
        "history": TECH_KUCHING_HISTORY,
        "query": "What are the latest developments in the Sarawak renewable energy sector?",
        "expect_followup": False,
        "expansion_contains": [],
        "note": ">=7-word self-contained query must not be treated as follow-up",
    },
    {
        "id": "both_topic_and_location_new",
        "category": "follow-up classification",
        "history": ACCIDENT_SIBU_HISTORY,
        "query": "any flood updates in Miri?",
        "expect_followup": False,
        "expansion_contains": [],
        "note": "query with BOTH new topic and new location is treated as standalone (documented behavior)",
    },
    {
        "id": "greeting_phrase",
        "category": "robustness",
        "history": TECH_KUCHING_HISTORY,
        "query": "hello there",
        "expect_followup": False,
        "expansion_contains": [],
        "note": "greeting-only phrase is not misclassified as a follow-up (greeting filler guard)",
    },
    {
        "id": "bm_followup",
        "category": "follow-up expansion",
        "history": TECH_KUCHING_HISTORY,
        "query": "macam mana pula di Miri?",
        "expect_followup": True,
        "expansion_contains": ["tech", "miri"],
        "note": "Bahasa Malaysia follow-up marker + location swap keeps carried-over topic",
    },
]


def run_tier1() -> dict:
    from src.core.conversation_service import expand_followup_query, is_followup_query

    results = []
    passed = failed = 0
    for case in TIER1_CASES:
        history = case["history"]
        got_followup = is_followup_query(case["query"], history)
        expanded, _ = expand_followup_query(case["query"], history)

        problems = []
        if got_followup != case["expect_followup"]:
            problems.append(
                f"followup: expected {case['expect_followup']}, got {got_followup}"
            )
        for needle in case["expansion_contains"]:
            if needle not in expanded.lower():
                problems.append(f"expansion missing {needle!r}: {expanded!r}")

        ok = not problems
        if case.get("expected_fail"):
            # Documented limitation: the heuristic failing IS the expected result.
            ok = True
            problems.append("documented limitation observed (heuristic fails as expected)")
        if ok and not case.get("expected_fail"):
            passed += 1
        else:
            failed += 1

        results.append(
            {
                "id": case["id"],
                "category": case["category"],
                "query": case["query"],
                "expected_followup": case["expect_followup"],
                "got_followup": got_followup,
                "expanded_query": expanded,
                "ok": ok,
                "expected_fail": bool(case.get("expected_fail")),
                "problems": problems,
                "note": case["note"],
            }
        )
        status = "ok" if ok else "FAIL"
        print(f"  [{status}] {case['id']}: {case['query']!r} -> {expanded!r}")

    total = len(TIER1_CASES)
    summary = {
        "cases": total,
        "passed": passed,
        "failed": failed,
        "documented_limitations": sum(1 for c in TIER1_CASES if c.get("expected_fail")),
        "pass_rate_excluding_documented": round(passed / (total - sum(1 for c in TIER1_CASES if c.get("expected_fail"))), 4),
    }
    print(f"Tier 1: {passed}/{total - summary['documented_limitations']} heuristic cases passed "
          f"({summary['documented_limitations']} documented limitations)")
    return {"summary": summary, "results": results}


# --------------------------------------------------------------------------- #
# Tier 2: scripted end-to-end conversations
# --------------------------------------------------------------------------- #
# Each conversation is replayed turn by turn, exactly like handlers.py
# conversational_message: accumulate history, accumulate shown links, exclude
# previously shown links from later turns.
TIER2_CONVERSATIONS: list[dict] = [
    {
        "id": "tech_location_thread",
        "turns": [
            "Any tech news in Kuching?",
            "how about Sibu?",
            "tell me more",
        ],
    },
    {
        "id": "incident_wh_thread",
        "turns": [
            "Any accident news in Sibu?",
            "why did it happen?",
            "when was the latest one?",
        ],
    },
    {
        "id": "topic_gate_abstention",
        "turns": [
            "Any news about the Kuching aquarium expansion?",
            "how about Miri?",
        ],
    },
    {
        "id": "malay_single_shot",
        "turns": [
            "banjir di Miri",
        ],
    },
    {
        "id": "temporal_thread",
        "turns": [
            "any flood news today?",
            "what about yesterday?",
        ],
    },
    {
        "id": "long_selfcontained_after_thread",
        "turns": [
            "Any tech news in Kuching?",
            "What are the latest developments in the Sarawak renewable energy sector?",
        ],
    },
]


def _plain_text_from_html(html: str) -> str:
    import re

    text = re.sub(r"<[^>]+>", " ", html or "")
    return " ".join(text.split())


def run_tier2() -> dict:
    from sqlalchemy import func, select, text as sql_text

    from src.core.conversation_service import expand_followup_query
    from src.core.models import NewsArticle
    from src.core.services import get_news_agent_response_for_user
    from src.storage.database import SessionLocal

    with SessionLocal() as s:
        n_articles = s.execute(
            select(func.count()).select_from(NewsArticle)
        ).scalar_one()
        n_with_summary = s.execute(
            select(func.count())
            .select_from(NewsArticle)
            .where(NewsArticle.ai_summary.isnot(None))
        ).scalar_one()
    print(f"DB: {n_articles} articles, {n_with_summary} with ai_summary")
    if n_articles == 0:
        print("WARNING: database is empty; tier 2 answers will use the RSS fallback path.")

    rows: list[dict] = []
    conv_metrics: list[dict] = []

    for conv in TIER2_CONVERSATIONS:
        history: list[dict] = []
        shown_so_far: list[str] = []
        conv_novelty: list[float] = []
        print(f"\n== conversation: {conv['id']} ==")
        for i, user_text in enumerate(conv["turns"], start=1):
            expanded, _ = expand_followup_query(user_text, history)
            t0 = time.time()
            html, shown = get_news_agent_response_for_user(
                BENCH_TELEGRAM_ID,
                expanded or user_text,
                conversation_history=history,
                exclude_article_links=shown_so_far or None,
            )
            elapsed = round(time.time() - t0, 2)

            prior = set(shown_so_far)
            novel = [lk for lk in shown if lk not in prior]
            novelty = round(len(novel) / len(shown), 4) if shown else None
            if shown:
                conv_novelty.append(novelty)
            abstained = "couldn't find" in (html or "").lower()

            rows.append(
                {
                    "conversation": conv["id"],
                    "turn": i,
                    "user_text": user_text,
                    "expanded_query": expanded,
                    "response_html": html,
                    "shown_links": " | ".join(shown),
                    "shown_count": len(shown),
                    "novelty": novelty,
                    "abstained": abstained,
                    "elapsed_s": elapsed,
                }
            )
            print(
                f"  turn {i}: {user_text!r} -> expanded {expanded!r} | "
                f"shown={len(shown)} novelty={novelty} abstained={abstained} {elapsed}s"
            )

            history.append({"role": "user", "content": user_text, "interaction_type": ""})
            history.append(
                {
                    "role": "assistant",
                    "content": _plain_text_from_html(html),
                    "interaction_type": "qa",
                    "shown_article_links": shown,
                }
            )
            for lk in shown:
                if lk not in shown_so_far:
                    shown_so_far.append(lk)

        conv_metrics.append(
            {
                "conversation": conv["id"],
                "turns": len(conv["turns"]),
                "mean_novelty": round(sum(conv_novelty) / len(conv_novelty), 4) if conv_novelty else None,
                "abstentions": sum(
                    1 for r in rows if r["conversation"] == conv["id"] and r["abstained"]
                ),
            }
        )

    all_novelty = [r["novelty"] for r in rows if r["novelty"] is not None]
    return {
        "db_articles": n_articles,
        "db_with_summary": n_with_summary,
        "mean_novelty": round(sum(all_novelty) / len(all_novelty), 4) if all_novelty else None,
        "abstention_turns": sum(1 for r in rows if r["abstained"]),
        "total_turns": len(rows),
        "conversations": conv_metrics,
        "rows": rows,
    }


# --------------------------------------------------------------------------- #
def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--tier2", action="store_true", help="Also run end-to-end conversations (DB + LLM)"
    )
    parser.add_argument(
        "--tier2-only", action="store_true", help="Run only the end-to-end tier"
    )
    args = parser.parse_args()

    run_t1 = not args.tier2_only
    run_t2 = args.tier2 or args.tier2_only

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    BENCH_DIR.mkdir(parents=True, exist_ok=True)

    if run_t1:
        print("== Tier 1: follow-up heuristics ==")
        tier1 = run_tier1()
        t1_path = BENCH_DIR / f"conversation_tier1_{stamp}.json"
        t1_path.write_text(json.dumps(tier1, indent=2), encoding="utf-8")
        print(f"Tier 1 results -> {t1_path}")

    if run_t2:
        print("\n== Tier 2: end-to-end conversations ==")
        tier2 = run_tier2()
        rows = tier2.pop("rows")

        csv_path = BENCH_DIR / f"conversation_e2e_{stamp}.csv"
        with csv_path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(
                fh,
                fieldnames=[
                    "conversation", "turn", "user_text", "expanded_query",
                    "response_html", "shown_links", "shown_count",
                    "novelty", "abstained", "elapsed_s",
                ],
            )
            writer.writeheader()
            writer.writerows(rows)

        meta_path = BENCH_DIR / f"conversation_e2e_{stamp}.json"
        meta_path.write_text(
            json.dumps(
                {
                    "generated_utc": stamp,
                    "note": "Responses await manual 1-5 rubric scoring (relevance, coherence, "
                            "factual consistency). novelty/abstained are auto-metrics.",
                    **tier2,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"\nTier 2 responses -> {csv_path}")
        print(f"Tier 2 metadata  -> {meta_path}")
        print(
            f"auto-metrics: mean_novelty={tier2['mean_novelty']} "
            f"abstentions={tier2['abstention_turns']}/{tier2['total_turns']} turns"
        )


if __name__ == "__main__":
    main()
