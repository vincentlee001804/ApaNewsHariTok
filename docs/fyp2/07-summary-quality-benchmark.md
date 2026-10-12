# Phase 2 — Summary Quality Benchmark (Four-Dimension Framework)

**Thesis anchor:** §5.5 Future Works — "Evaluate AI summaries for relevance, coherence,
factual consistency, and compression"; supports limitation #5 (summary consistency).
**Date:** 2026-10-12 (FYP 2, Week 2)
**Status:** complete (n = 10 paired articles, automatic metrics + cross-model LLM judging).

---

## 1. Goal

Phase 1 (doc 01) measured only **length consistency**. This phase executes the full
four-dimension evaluation the thesis promises, comparing the two models the system
actually deploys:

| Arm | Model | Hosting | Role |
|---|---|---|---|
| `llama31_v2` | llama3.1 (8B) | local Ollama (self-hosted) | primary; thesis "local AI" identity |
| `gptoss_v2` | gpt-oss:20b | Ollama Cloud (remote API) | production cloud fallback |

The local-vs-cloud pairing doubles as evidence for the self-hosting future-work
narrative: it measures what a self-hosted open SLM gives up (or keeps) versus a
larger cloud-hosted open model behind the same prompt and cleanup pipeline.

**Scope correction vs. earlier plan:** MiMo (fallback_2) was dropped — it is not a
model the bot is deployed with, and its API key is invalid (401). Benchmarking it
would not be citable as production evidence.

## 2. Method

- **Fixture:** the same 50-article fixture as Phase 1 (`benchmark/articles_fixture.json`,
  seed 42); this phase uses the **first 10 articles** so the two arms are paired
  article-for-article. Inputs are RSS descriptions (see the Phase 1 proxy note).
- **Pipeline:** production prompt v2 + strict retry + `finalize_summary_candidate`
  cleanup via `scripts/benchmark_summaries.py` — identical to what the bot ships.
- **Quality metrics** (`scripts/benchmark_summary_quality.py`), per produced summary:
  - *Compression* — summary_words / source_words.
  - *Relevance proxy* — token-level ROUGE-L (precision / recall / F1) against the source.
  - *Factual-consistency proxy* — fraction of summary content tokens, and of numeric
    tokens, present in the source (case-insensitive).
  - *Coverage behavior* — abstention rate (`no_summary: true` rows).
- **LLM judging (1–5 rubric):** cross-judge design so no model grades its own output —
  gpt-oss:20b (cloud) judges the 10 llama3.1 summaries; llama3.1 (local) judges the
  5 gpt-oss summaries. Dimensions: relevance, coherence, factuality (rubric in script).

### 2.1 Operational finding: reasoning-model token budget (production-relevant)

`gpt-oss:20b` is a reasoning model: it spends output tokens on hidden reasoning before
the visible answer. Under the production `OLLAMA_SUMMARY_NUM_PREDICT=384` cap, cloud
responses returned **empty output** (`done_reason: length`, `eval_count: 384`) — i.e.,
the production cloud fallback as currently tuned cannot produce summaries at all.
The benchmark arm therefore ran with a benchmark-only override of 1024 tokens
(`scripts/_cloud_bench.py` sets the env var; production config and `.env` untouched).
**Follow-up recommendation:** raise `OLLAMA_SUMMARY_NUM_PREDICT` (or make it
model-dependent) before relying on the gpt-oss fallback in production.

## 3. Results

### 3.1 Automatic metrics (paired n = 10)

| Metric | llama3.1 (local) | gpt-oss:20b (cloud) |
|---|---|---|
| Summaries produced | 10 / 10 | 5 / 10 |
| Abstention rate | 0.0 | 0.50 |
| Mean summary length (words) | 20.4 | 19.0 |
| Mean compression ratio (↓ = more compressed) | 0.70 | 0.47 |
| ROUGE-L F1 (vs source) | 0.242 | 0.177 |
| ROUGE-L recall | 0.194 | 0.124 |
| Content-token containment | 0.476 | 0.443 |
| Numeric-token containment | 1.00 | 1.00 |

All 10 source texts are short (14–79 words, RSS descriptions), which caps how much
compression is possible; the ratios should be read against that ceiling.

### 3.2 LLM-judged rubric (1–5, cross-judged)

| Arm (judge) | n | Relevance | Coherence | Factuality |
|---|---|---|---|---|
| llama3.1 summaries, judged by gpt-oss:20b | 10 | 3.9 | 4.7 | 3.3 |
| gpt-oss:20b summaries, judged by llama3.1 | 5 | 5.0 | 5.0 | 5.0 |

Per-article detail is in `benchmark/judge_scores_20261012T085759Z.csv` (llama arm) and
`benchmark/judge_scores_20261012T085616Z.csv` (gpt-oss arm).

## 4. Interpretation

1. **The headline finding is behavioral, not lexical.** The four factuality scores of 1
   and one of 2 that gpt-oss gave llama3.1 all occur on the *thinnest inputs* (14–55-word
   descriptions: articles 423257, 427773, 431945, 412074; a 3 on the 28-word 406211).
   gpt-oss abstained on exactly those inputs. Conclusion: llama3.1 **always summarizes,
   and fabricates when the input is thin**; gpt-oss **declines to answer** instead.
   For a news-delivery bot, gpt-oss's precision-first behavior is the safer failure
   mode — but a 50% abstention rate on description-length inputs would mean half of
   summarized items deliver nothing, so neither behavior is directly acceptable.
2. **On adequate inputs both models are strong.** On the two articles where both
   models produced summaries (426583, 407993), the stricter judge scored llama3.1
   5/5/5 and 5/4/5 (relevance/coherence/factuality). llama3.1's coherence stayed high
   (4.7) even on weak articles — the failure mode is factual, not linguistic.
3. **Judge-leniency asymmetry is real** (llama-as-judge gave all 5s, n = 5). Absolute
   scores should be treated cautiously; the reliable signal is the *strict judge's*
   relative pattern (factuality tracking input thinness) corroborated by the
   abstention data and the containment proxy.
4. **Numbers are safe in both models** (numeric containment 1.00 both arms) — dates,
   counts, and quantities in summaries were always traceable to the source.

### 4.1 Tie-back to thesis

- Supports **limitation #5** (summary consistency): the v2 prompt + retry (Phase 1)
  fixed length; this phase shows the remaining quality risk is *factual reliability on
  thin inputs*, not fluency.
- Feeds **future works**: stronger small models with built-in abstention calibration
  (Qwen / Ministral line, as §5.5 names) would let the bot keep local hosting while
  gaining gpt-oss-like refusal behavior; until then, the cloud fallback path needs the
  token-budget fix in §2.1.

## 5. Threats to validity

- **n = 10** paired articles; results are indicative, not statistical. The fixture has
  50 articles if a larger run is wanted.
- **Input proxy:** RSS descriptions, not scraped full bodies; thin inputs are
  over-represented vs production. Abstention rates should be re-measured on full bodies.
- **Automatic proxies** reward extractive overlap; paraphrase and inference are
  under-credited (visible in llama's ROUGE-L being higher than its judged factuality).
- **Single judge per direction**, asymmetric judge leniency; no human gold standard yet
  (the planned ~150-respondent evaluation can incorporate one).
- **Benchmark-only token cap** for gpt-oss (§2.1): length results for the cloud arm
  reflect a 1024-token budget, not production's 384.

## 6. Reproduction

```powershell
# 1. local arm (llama3.1, production settings) — already existed from Phase 1
python scripts/benchmark_summaries.py run --models llama3.1:latest `
    --label llama31_v2 --offset 0 --limit 10 --out docs/fyp2/benchmark/llama31_v2.csv

# 2. cloud arm (gpt-oss:20b via configured Ollama Cloud fallback, 1024-token override)
python scripts/_cloud_bench.py run -- run --models gpt-oss:20b `
    --label gptoss_v2 --prompt-version v2 --offset 0 --limit 10 --out docs/fyp2/benchmark/gptoss_v2.csv

# 3. quality metrics + cross-model judging (local Ollama must be up for the llama judge)
python scripts/benchmark_summary_quality.py `
    docs/fyp2/benchmark/llama31_v2.csv docs/fyp2/benchmark/gptoss_v2.csv --judge
```

Artifacts (all under `docs/fyp2/benchmark/`): `gptoss_v2.csv`,
`summary_quality_scores.csv`, `quality_metrics_20261012T085412Z.json`,
`judge_scores_20261012T085616Z.csv`, `judge_scores_20261012T085759Z.csv`,
`judge_metrics_20261012T090039Z.json`.
