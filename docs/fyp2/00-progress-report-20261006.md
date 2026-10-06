# FYP 2 Progress Report — 2026-10-06 (End of Week 1)

**Project:** Apa News Hari Tok? (Telegram news bot for Sarawak local news)
**Author:** Vincent Lee Cheng Hong (BCS24020018)
**Period covered:** 2026-10-05 → 2026-10-06 (FYP 2 start, Week 1)
**Status:** Phases 1 and 2 complete and evaluated; Phase 3 (semantic dedup) starting.
**Code:** `main` @ `e513c53` on GitHub (vincentlee001804/ApaNewsHariTok)

---

## 1. Infrastructure changes (pre-phase, 2026-10-05)

- Database migrated from SQLite to **Supabase Postgres** (free tier, Seoul region,
  pgvector 0.8.0 enabled). Access via the IPv4-compatible connection pooler
  (`aws-1-ap-northeast-2.pooler.supabase.com:6543`) — the direct `:5432` host is
  unreachable from the campus network (IPv6-only), which motivated the pooler setup.
- Deployment moved from Fly.io to **Oracle Cloud (Docker)**; GitHub-push auto-deploy set
  up on 2026-10-05, then **paused by owner** pending FYP 2 model-cost decisions (Ollama
  cloud quota exhausted; local Ollama is the dev/test path).
- All schema migrations verified idempotent (Postgres-only, dialect-guarded); SQLite
  backward compatibility preserved per project change rules.

## 2. Phase 1 — Summary-length consistency (thesis priority #4) — DONE

**Problem (thesis §5.4):** the local LLM occasionally misses the 30-word summary limit.

**Changes** (`src/ai/summarizer.py`):
- v2 prompt: explicit word budget + output contract ("one plain sentence, ≤30 words").
- Server-side enforcement: mid-sentence clip now triggers one constrained retry.

**Evaluation** (`scripts/benchmark_summaries.py`, n=10 articles, local llama3.1):
- v1: 100% within cap, mean 20.5 words; v2: 100% within cap, mean 20.4 words;
  1 retry triggered and corrected a 31-word output to 30.
- Evidence: `benchmark/metrics_20261005T091303Z.json`, `01-summary-length-consistency.md`.
- Production-model (mimo) run intentionally deferred to save API quota.

## 3. Phase 2 — pgvector RAG + hybrid retrieval (thesis priority #1) — DONE

**Problem (thesis §5.4):** in-memory retrieval over ≤15 candidates misses context for
complex questions.

**Changes:**
- New `article_embeddings` table (768-dim vectors, HNSW cosine index, idempotent
  migration); embeddings created at ingestion; full production backfill of **1,692/1,692
  articles** completed.
- `semantic_rank_articles()` now uses one SQL cosine top-k over stored vectors with
  graceful fallback to the FYP 1 in-memory path (feature off / SQLite / table missing /
  Ollama down / SQL error).
- **Hybrid retrieval** (owner-approved addition, 2026-10-06 — beyond the original plan,
  driven by measured diagnosis): metadata boosts (location 0.15 / category 0.10 /
  keyword 0.25) + a metadata recall union (location match or ≥50% distinctive-token
  overlap enters the rescored set regardless of cosine) + anywhere-in-question location
  scan + acronym tokens (SEZ/API).
- News-agent evidence window widened 24h → 30-day corpus (`NEWS_AGENT_WINDOW_HOURS`);
  semantic pool 15 → 400 (`RAG_AGENT_POOL_SIZE`).

**Evaluation** (fixed 10 real questions, production corpus; `scripts/eval_rag_pools.py`):

| Path | Relevant article in top-10 |
|---|---|
| FYP 1 (24h window) | 1 / 10 |
| FYP 2 vector only | 3 / 10 |
| **FYP 2 hybrid** | **6 / 10** |

- The 4 remaining single-article misses still surface the correct *story* in the top-10
  (10/10 same-story articles for Long Lellang crash and Kuching arson questions) —
  answer quality is higher than the strict metric suggests.
- **End-to-end confirmation (2026-10-06, live Telegram):** "Was there a helicopter crash
  at Long Lellang?" → correct, sourced answer from the 26-day-old story; the FYP 1
  baseline for this question was "no related news".
- Process record includes two documented negative-result iterations (boost-only hybrid
  scored 3/10 = no change → recall union designed; "will" stopword leak → fixed).
- Full detail: `02-pgvector-rag.md` §4, benchmark run log + `rag_pool_hybrid_20261006.txt`.

## 4. Known issues / decisions

- Supabase DB password was pasted in chat on 2026-10-05; **rotation postponed by owner**
  (needs Oracle container env update; Phase 0 hygiene item, still open).
- Oracle + Fly deployments both paused; only the local laptop bot is live.
- Residual RAG limitations documented in 02 §4.5: cross-lingual ranking gap, embedding
  ceiling on low-cosine articles, sibling-article competition within big stories.

## 5. Next (Week 2 onward)

- ~~**Phase 3 — semantic dedup**~~ **DONE 2026-10-06** (same day): cosine same-story pass
  on stored vectors, threshold 0.85 from 249 labeled pairs; 14-day clustering simulation
  312 → 249 clusters with 71 semantic-only merges incl. cross-lingual pairs. Full record:
  `03-semantic-dedup.md`.
- Phase 4 — real-time official alerts (time-boxed; highest risk).
- Phase 5 — evaluation expansion 86 → 150+ respondents; human-labeled dedup tuning set;
  deferred Phase 1 mimo benchmark.
