# FYP 2 — Change Log & Evidence Index

Every FYP 2 change is documented here with its motivation, code location, and how to reproduce
its evaluation. Individual phase documents hold the full detail; benchmark artifacts live under
`docs/fyp2/benchmark/`.

| # | Phase | Document | Status |
|---|-------|----------|--------|
| — | **Progress report (Week 1)** | [00-progress-report-20261006](00-progress-report-20261006.md) | Phases 1–2 done + evaluated; Telegram end-to-end confirmed |
| 1 | Summary-length consistency | [01-summary-length-consistency](01-summary-length-consistency.md) | Code done 2026-10-05; local llama3.1 benchmark run (n=10); production-model run pending |
| 2 | pgvector RAG | [02-pgvector-rag](02-pgvector-rag.md) | Complete 2026-10-06: vector search + **hybrid retrieval** (metadata boosts + recall union) + 30-day news-agent window. Pool coverage 1/10 (FYP1) → 3/10 (vector) → **6/10 (hybrid)**; right-story top-10s on 9–10/10 |
| 3 | Semantic dedup | [03-semantic-dedup](03-semantic-dedup.md) | Complete 2026-10-06: cosine same-story pass on stored vectors (threshold 0.85, evidence-based); clustering sim 312 → 249 clusters on a 14-day batch, cross-lingual duplicates caught |
| 4 | Utility disruption alerts | [04-utility-alerts](04-utility-alerts.md) | Stage 1 (shadow mode) live 2026-10-06: polls swbnews Telegram + JBALB + Google News RSS every 10 min, classifies water/power disruption notices, stores alerts, computes deliveries but sends nothing until Stage 2 approval |
| 5 | Fly.io shadow deployment | [05-fly-shadow-deployment](05-fly-shadow-deployment.md) | Week 2 progress report (2026-10-11): FYP2 stack shadow-deployed on Fly.io alongside production |
| 6 | Progress report (Week 2) | [06-week2-start](06-week2-start.md) | Week 2 progress report (2026-10-12): status of phases 1–4 ahead of go-live decisions |
| 7 | Summary quality benchmark | [07-summary-quality-benchmark](07-summary-quality-benchmark.md) | Four-dimension framework (relevance, coherence, factual consistency, compression) for AI summary evaluation per thesis §5.5 |
| 8 | Utility alerts Stage 2 sign-off | [08-utility-alerts-stage2-signoff](08-utility-alerts-stage2-signoff.md) | Shadow-evidence review approving go-live: false-positive/negative checks and delivery-staging audit |
| 9 | Feed source audit & fetch fix | [09-feed-source-audit](09-feed-source-audit.md) | 2026-10-14: audit found only 13/18 sources returning news on Fly (1 dead feed, 1 UA-blocked, 3 IP-blocked). Fixed UA in `rss_reader.py`, disabled dead rss.app feed, added 3 verified sources (Astro Awani, Berita Harian, The Sun), VM 512MB→1GB → 17/20 active sources after redeploy; 3 IP-blocked + no-RSS publishers documented with options |
| 10 | X official alerts via twitterapi.io | [10-x-alerts-twitterapi](10-x-alerts-twitterapi.md) | 2026-10-14: 4th utility-alert source polls `@1SarawakEnergy` (~$3–5/mo pay-per-use, vs ~$100 official API). Sarawak Water N/S are Facebook-only → documented limitation; vendor-lock-in risk recorded with mitigation |
| 11 | Account deletion + poll frequency | [11-account-deletion-and-poll-frequency](11-account-deletion-and-poll-frequency.md) | 2026-10-15: ON DELETE CASCADE migration on the 3 users FKs + `/deleteaccount` self-delete command; test user 123456 cleaned up (killed recurring `Chat not found` spam). Utility-alert poll 10→30 min via Fly secret (~$1–2/mo X cost, ~3x longer bonus credits) |
| 12 | Utility alerts Stage 2 go-live | [12-utility-alerts-go-live](12-utility-alerts-go-live.md) | Flipped 2026-10-12 (owner-approved per doc 08); 4-day live evidence: 934 real deliveries, 52 users reached, 7 opted out of 99, opt-out filter verified in delivery path |
| 13 | Conversation memory & conversational QA | [13-conversation-memory](13-conversation-memory.md) | 2026-11-09→11: conversation turns + follow-up heuristics (EN/BM), history-aware news agent, topic-relevance gate + refusal UX, per-conversation shown-article dedup. Tier 1 heuristics 12/12; tier 2 e2e relevance 2.31→3.62, factual 3.31→4.0, false negatives 3→0 |

**Code baseline:** all FYP 2 work branches from the production system as of 2026-10-05
(Oracle Cloud Docker + Supabase Postgres, 100 pilot users, pipeline live since 2026-09-09).
