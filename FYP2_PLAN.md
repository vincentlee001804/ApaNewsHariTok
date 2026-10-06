# FYP 2 Work Plan — Apa News Hari Tok?

**Date:** 2026-10-05 (FYP 2 start)
**Baseline:** FYP 1 MVP in production — Oracle Cloud (Docker) + Supabase Postgres, 100 pilot users, news pipeline live since 2026-09-09.

---

## 1. Where FYP 1 ended (measured baseline)

| Component | Current state (file) | Known weakness (from thesis §5.4) |
|---|---|---|
| RAG retrieval | In-memory cosine over ≤15 candidates, per-query Ollama embeddings, no persistence (`src/ai/retriever.py`) | Misses context for complex questions; dies with process restarts; re-embeds everything every query |
| Dedup | Jaccard token overlap, title/body/AI-summary thresholds (`src/core/services.py`) | Same incident, different wording → repeat notifications |
| Alerts | RSS/Telegram prefetch every 15 min + Waze | Closed official platforms arrive late |
| Summaries | Llama 3.1 via Ollama, ~30-word target, soft enforcement (`src/ai/summarizer.py`) | Model occasionally misses the 30-word limit |
| Hosting | Was laptop/Fly; **now Oracle Docker + Supabase** (done early) | — |
| Evaluation | 86 respondents (convenience sample) | Not generalizable; needs 150+ |

---

## 2. Milestones

### Phase 0 — Hygiene (Week 1, no new features)
- Rotate Supabase DB password (exposed in chat 2026-10-05); update Oracle container env + local `.env`.
- Decide local dev policy: strip `DATABASE_URL` from local `.env` → laptop dev on `mvp.db` sandbox.
- Set up a scheduled `pg_dump` backup — Supabase free tier has no automated backups.

### Phase 1 — Summary-length consistency (Weeks 1–4)
*Thesis priority #4. Cheapest, most measurable, feeds the evaluation chapter.*

- Tighten the summarization prompt in `summarize()` (`src/ai/summarizer.py:428`): explicit word budget, hard instruction, output contract ("one plain sentence, ≤30 words").
- Add server-side enforcement: if `clip_plain_text_to_word_limit()` has to cut mid-sentence, retry once with a stronger constraint; count retries in logs.
- **Model benchmark:** script that runs N≈50 stored articles through each candidate model (current `llama3.1`, plus `qwen2.5:3b`-class and `ministral`-class per thesis) and scores: % within 30-word target, human rubric (relevance, coherence, factual consistency, compression — the four metrics named in the thesis).
- Deliverable: model selection rationale for the final system.

### Phase 2 — pgvector RAG (Weeks 4–10)
*Thesis priority #1. Core technical contribution.*

- Enable the `vector` extension on Supabase (free tier supports it).
- New table `article_chunks` via idempotent migration in `src/storage/migrate.py` (per change rule #2): `(id, article_id FK, chunk_index, chunk_text, embedding vector(n), created_at)` with IVFFlat/HNSW index.
- Embedding backfill job: on prefetch insert and as a one-shot backfill over existing `news_articles`, chunk (title+summary first; section-level later), embed with existing `OLLAMA_EMBED_MODEL`, upsert vectors. Graceful skip when Ollama is down (rule #8).
- Rewrite `src/ai/retriever.py`: replace per-query re-embedding of the whole candidate pool with SQL `<=>` cosine top-k over stored vectors; keep Ollama embedding only for the user query. Cache stays only as a per-process query cache.
- Config additions in `src/core/config.py`: `RAG_VECTOR_ENABLED`, `RAG_CHUNK_*`, backfill batch sizes. Degrade to today's in-memory path when disabled/unavailable.
- Evaluation: fixed question set (20–30 real user-style questions), compare retrieval quality before/after (hit-rate of the article a human would pick).

### Phase 3 — Semantic dedup (Weeks 6–12, overlaps Phase 2)
*Thesis priority #2. Reuses the pgvector work.*

- Keep today's Jaccard pass as a cheap prefilter, then run embedding similarity (same `article_chunks`/article-level vectors from Phase 2) on survivors: same-story pairs get flagged above a tuned cosine threshold.
- Tune against a labeled set: pull known duplicate pairs from production `news_articles` (same incident, multiple publishers) + known distinct pairs; pick threshold that minimizes both repeat notifications and false merges.
- Config: `CROSS_SOURCE_DEDUP_SEMANTIC_ENABLED`, `..._COSINE_THRESHOLD` in `config.py`.
- Evaluation metric: duplicate-repeat rate before/after, measured on live traffic over a fixed window.

### Phase 4 — Real-time official alerts (Weeks 10–14, time-boxed)
*Thesis priority #3. Highest risk, lowest certainty — schedule last.*

- Scope narrowly: pick ONE official source with an accessible API or approved channel (e.g. a utility/authority with RSS/API), integrate as a new scraper in `src/scrapers/`.
- If no source is approvable within the time-box, document the attempt, the platform restrictions found, and keep Waze + RSS as the alert path — an honest "future work" section is acceptable here.

### Phase 5 — Evaluation expansion (Weeks 12–16, parallel)
- Grow sample from 86 → 150+ respondents (power/target justification for the thesis).
- Include the Phase 1 summary-quality study (rubric-scored summaries, before/after model swap).
- Re-run the usefulness/ease-of-use instrument; compare against FYP 1 baseline.

---

## 3. Standing constraints (from AGENTS.md — apply to all phases)

1. Config only via `src/core/config.py`; DB changes via `models.py` + idempotent `migrate.py`.
2. Three dedup layers preserved; per-user delivery stays in `UserArticleDelivery`.
3. Blocking work in `asyncio.to_thread`; HTML-escape Telegram output.
4. Graceful degradation when Ollama is unavailable.
5. Evening-only digest policy; quiet when no new content.
6. Location aliases updated in both `location_extractor.py` and `ai/summarizer.py`.
7. `python -m compileall src` after changes; never run live fetch/push without explicit approval.
8. Secrets stay out of git and logs.

## 4. Ops notes for Oracle + Supabase setup

- Deploys = rebuild image + `docker run`/`compose up -d` with env; check whether `.env` was baked in by `COPY . .`.
- `DATABASE_URL` from Oracle should use the pooler (`aws-1-ap-northeast-2.pooler.supabase.com:6543`, user `postgres.rlsbshvvaksjdiumwndm`) — IPv4-safe.
- Migrations run automatically at bot startup via `init_db()` — ensure they are idempotent before shipping.
- Backup: nightly `pg_dump` cron on the Oracle box, retained 14 days.

## 5. Risk register

| Risk | Mitigation |
|---|---|
| Ollama embedding unavailable on Oracle (no GPU) → slow backfill | Batch + rate-limit backfill; run once on laptop against Supabase |
| pgvector extension blocked on free tier | Fall back to storing vectors as `float4[]`/`jsonb` + in-SQL dot product |
| Semantic dedup false-merges distinct stories | Conservative threshold; Jaccard prefilter retained; shadow-mode logging before enforcing |
| Phase 4 source approval stalls | Time-box; documented negative result is an acceptable thesis outcome |
| Migration breaks production startup | Test `init_db()` against a fresh local Postgres container before deploy |
