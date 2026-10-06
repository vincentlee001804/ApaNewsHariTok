# FYP 2 — Change Log & Evidence Index

Every FYP 2 change is documented here with its motivation, code location, and how to reproduce
its evaluation. Individual phase documents hold the full detail; benchmark artifacts live under
`docs/fyp2/benchmark/`.

| # | Phase | Document | Status |
|---|-------|----------|--------|
| 1 | Summary-length consistency | [01-summary-length-consistency](01-summary-length-consistency.md) | Code done 2026-10-05; local llama3.1 benchmark run (n=10); production-model run pending |
| 2 | pgvector RAG | [02-pgvector-rag](02-pgvector-rag.md) | Complete 2026-10-06: vector search + **hybrid retrieval** (metadata boosts + recall union) + 30-day news-agent window. Pool coverage 1/10 (FYP1) → 3/10 (vector) → **6/10 (hybrid)**; right-story top-10s on 9–10/10 |

**Code baseline:** all FYP 2 work branches from the production system as of 2026-10-05
(Oracle Cloud Docker + Supabase Postgres, 100 pilot users, pipeline live since 2026-09-09).
