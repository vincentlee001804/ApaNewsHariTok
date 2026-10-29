# AGENTS.md

## Project overview

**Apa News Hari Tok?** is a Python Telegram bot for collecting, summarizing, filtering, and delivering Sarawak local news. It is an FYP MVP that uses RSS feeds and approved Telegram sources, SQLAlchemy storage, and Ollama for optional AI summaries, categorization, and retrieval-assisted Q&A.

Treat this as a working application: make small, compatible changes rather than broad rewrites.

## Repository map

```text
src/
  bot/
    bot_main.py       Application entry point; registers handlers and scheduled jobs.
    handlers.py       Telegram commands, callbacks, onboarding, and conversation flow.
  core/
    config.py         Centralized environment/configuration parsing.
    models.py         SQLAlchemy models.
    services.py       Selection, filtering, delivery, deduplication, and Q&A logic.
    user_service.py   User/preference CRUD helpers.
    prefetch_service.py / cleanup_service.py / summary_health_service.py
                       Background ingestion, retention, and AI-summary outage checks.
    news_categories.py / location_extractor.py / local_keywords.py / rss_limits.py
                       Shared taxonomy, location extraction, keyword and feed-limit helpers.
    utility_alert_service.py
                       FYP2: official disruption-alert polling and delivery staging.
  ai/
    summarizer.py     Ollama calls and AI prompt logic.
    retriever.py      FYP2: pgvector + hybrid retrieval for news Q&A.
  scrapers/
    rss_reader.py, article_scraper.py, telegram_reader.py, waze_client.py,
    utility_alert_reader.py   (FYP2: official-source fetchers incl. X via twitterapi.io + rule classifier)
  storage/
    database.py       Engine/session setup and startup initialization.
    migrate.py        Idempotent schema migrations.
scripts/              Dev/benchmark helpers (not shipped); see README section 7b.
                      `backfill_local_summaries.py` fills today's missing summaries via local Ollama.
docs/fyp2/            FYP2 per-phase docs and benchmark evidence.
```

Important root files:

- `RSS_Sources.txt`: RSS URLs and optional `telegram:` source entries.
- `Sarawak_Local_Keywords.txt`: low-cost local-relevance filter.
- `.env`: local credentials and runtime tuning; never commit it.
- `Dockerfile`, `fly.toml`, and `Procfile`: deployment configuration.
- `README.md`: original project requirements/specification.
- `docs/operations-reference.md`: detailed runtime, config, and data-model notes (merged from the former `AGENT.md`, 2026-10-06). Read it for anything not covered here.

## Setup and run

Use Python 3.10 or newer (production uses Python 3.12).

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m src.bot.bot_main
```

Run commands from the repository root. The bot uses absolute `src.*` imports.

The bot requires `TELEGRAM_BOT_TOKEN`. Ollama is optional for a basic run but needed for normal AI summaries and RAG. Configure all environment values through `.env` and `src/core/config.py`.

## Change rules

1. Keep configuration centralized in `src/core/config.py`; do not introduce scattered `os.getenv()` calls.
2. For a database change, update `src/core/models.py`, then add and register an idempotent migration in `src/storage/migrate.py`. Existing databases must start successfully after the change.
3. Add Telegram commands and callbacks in `src/bot/handlers.py`, and register them in `src/bot/bot_main.py`.
4. Reuse the taxonomy in `src/core/news_categories.py`; normalize AI-generated categories before storing or filtering them.
5. Preserve the three deduplication layers: unique article link, cross-source story matching, and per-user `UserArticleDelivery` records. Do not rely on `NewsArticle.last_sent_at` for user-specific delivery behavior.
6. Async handlers must run blocking database, HTTP, or LLM work using `asyncio.to_thread(...)` (or the established background-task pattern).
7. Escape dynamic content before sending Telegram messages in HTML parse mode. For callback edits, use the safe edit helper already in `handlers.py`.
8. AI-dependent behavior must degrade gracefully when Ollama is unavailable: fall back to safe raw text or skip optional AI enrichment.

## Behavioral constraints

- Digest delivery is evening-only by product policy, even if legacy preference data indicates otherwise.
- Scheduled jobs are registered in `bot_main.py`: prefetch, per-user scheduled delivery, database cleanup, the private AI-summary outage watchdog, and the FYP2 utility-alert poll.
- The FYP2 utility-alert module runs in shadow mode by default (`UTILITY_ALERT_SHADOW_MODE=true`): alerts are stored and would-be deliveries computed, but nothing is pushed until the owner explicitly flips it to `false` (see `docs/fyp2/04-utility-alerts.md`).
- User-facing scheduled notifications should remain quiet when no qualifying new content exists.
- Telegram IDs use `BigInteger`; never reduce them to a 32-bit integer type.
- Location aliases are maintained in both `location_extractor.py` and `ai/summarizer.py`; update both when changing the supported location set.

## 5.4 Limitations

The following are thesis-recorded limitations of the FYP 1 prototype. Treat them as known product and technical risks when changing related code.

- **Evaluation scale:** The initial validation used a convenience sample of 86 respondents, so perceived usefulness and ease-of-use results are not fully generalizable to all Sarawak users.
- **Hosting uptime:** A personal machine cannot reliably provide continuous scraping and alert delivery. The project used cloud hosting as a practical workaround; production availability still depends on the configured hosting and model endpoints.
- **RAG retrieval quality:** The current retrieval is in-memory, uses Ollama embeddings, and searches a limited candidate pool. It can miss context needed for complex questions.
- **Cross-source duplicates:** Token/Jaccard similarity can fail when different publishers describe the same incident with different wording, causing repeat notifications.
- **Source coverage:** Standard RSS/HTML scraping does not provide dependable real-time extraction from closed social platforms. Official utility alerts may therefore arrive later than primary-source announcements.
- **Summary consistency:** The small local language model may occasionally miss the intended 30-word summary limit; prompt and output constraints need continued refinement.

## 5.5 Future works

The thesis proposes the following FYP 2 direction. These are planned improvements, not implicit requirements for unrelated maintenance work.

### FYP 2 technical priorities

The primary FYP 2 scope is to address technical limitations 2 through 5 above:

1. **RAG retrieval quality:** Adopt scalable vector search, stronger text chunking, and semantic retrieval so answers to complex news questions have better grounding.
2. **Cross-source duplicate detection:** Improve deduplication beyond token overlap so reports of the same incident from differently worded articles are recognized as one story.
3. **Real-time official alerts:** Integrate approved official social-media APIs where feasible for more timely utility and disruption notifications.
4. **Summary-length consistency:** Refine prompts and evaluate suitable small language models, including Qwen/Ministral, to make brief summaries consistently meet the 30-word target.

Secondary work includes maintaining stable 24/7 backend hosting and conducting a larger final evaluation, expanding from 86 pilot respondents toward 150. Evaluate AI summaries for relevance, coherence, factual consistency, and compression.

## Security and data safety

- Never commit `.env`, Telegram tokens, API keys, session strings, `*.session`, or `mvp.db`.
- Do not expose user chat IDs, private Telegram content, or credentials in logs, tests, documentation, or responses.
- Avoid manually editing the SQLite database. Use migrations or existing service-layer code.

## Validation

There is currently no automated unit-test suite. `scripts/test_sibuwb_bot.py` is a Telethon session helper, not a test.

For code changes, at minimum:

```powershell
python -m compileall src
```

When credentials and dependencies are available, also start the application with `python -m src.bot.bot_main` and confirm initialization completes. Do not run live fetch, backfill, or push commands unless the task explicitly requires external side effects.
