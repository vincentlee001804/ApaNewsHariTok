# Operations Reference — "Apa News Hari Tok?"

Detailed working notes for developers and AI agents: tech stack, runtime behavior,
configuration catalog, data model, and coding gotchas. The **rules that must be
followed when changing code** live in `AGENTS.md` (project root); this file is the
deeper reference. (Consolidated from the former `AGENT.md` on 2026-10-06.)

---

## 1. Tech stack

| Layer | Technology |
|---|---|
| Language | Python 3.10+ (production 3.12) |
| Bot framework | `python-telegram-bot` 21.6 (polling mode, not webhooks) |
| LLM | Ollama HTTP API (`/api/generate`, `/api/embeddings`), default `llama3.1`, embed model `nomic-embed-text`; up to 2 fallback endpoints (e.g. Ollama Cloud / MiMo) |
| Scraping | `feedparser` (RSS), `requests` + `beautifulsoup4`/`lxml` (HTML), `telethon` (Telegram user-client reads) |
| Database | SQLAlchemy 2.0 ORM; SQLite (`mvp.db`, WAL) locally, **Supabase Postgres** in production |
| Scheduling | `python-telegram-bot` job_queue (repeating jobs); APScheduler is in requirements but scheduling lives in `bot_main` |
| Config | `.env` via `python-dotenv`; sources in `RSS_Sources.txt`; keywords in `Sarawak_Local_Keywords.txt` |
| Deployment | Docker (`Dockerfile`). 2026-10: Oracle Cloud instance with GitHub-push auto-deploy (**paused 2026-10-05** while AI quota is conserved); `fly.toml`/`Procfile` retained for Fly.io/Heroku-style hosts |

## 2. Repository layout (file-by-file)

```text
src/
  bot/
    bot_main.py          # ENTRYPOINT `python -m src.bot.bot_main`; handlers + repeating jobs
    handlers.py          # all command/callback/message handlers (~2500 lines)
  core/
    config.py            # ALL configuration constants, env parsing, source-file loaders
    models.py            # SQLAlchemy models
    services.py          # selection, filtering, dedup, digest/Q&A builders (~3500 lines)
    user_service.py      # user/preference CRUD helpers
    conversation_service.py  # conversation memory: turn save/load, follow-up heuristics
    prefetch_service.py  # RSS + Telegram ingestion job
    cleanup_service.py   # retention-based deletion of old articles/deliveries
    summary_health_service.py  # AI-summary outage watchdog (private alert to owner)
    news_categories.py   # category taxonomy (labels <-> slugs)
    location_extractor.py# rule-based Sarawak location extraction
    local_keywords.py    # LOCAL_INTEREST_KEYWORDS matching helpers
    rss_limits.py        # per-feed item caps (first-boot vs steady state)
    utility_alert_service.py   # FYP2: poll orchestration, delivery staging (shadow mode)
  ai/
    summarizer.py        # Ollama prompts/calls: summaries, titles, categories, greetings (~1000 lines)
    retriever.py         # FYP2: pgvector + hybrid retrieval for news Q&A
  scrapers/
    rss_reader.py        # generic RSS fetching -> RssItem
    article_scraper.py   # full-article HTML body extraction
    telegram_reader.py   # Telethon channel reading
    waze_client.py       # unofficial Waze live-map georss API (dev/preview)
    utility_alert_reader.py    # FYP2: official-source fetchers + rule classifier
  storage/
    database.py          # engine/session setup, SQLite pragmas, init_db() + migration runner
    migrate.py           # idempotent add-column/create-table migrations (run at startup)
scripts/                 # dev & benchmark helpers (not shipped) — see README §7b
docs/fyp2/               # FYP2 per-phase docs + benchmark evidence
logs/  tools/            # local-only, gitignored
```

## 3. How the running system works

### Repeating jobs (`bot_main.py`)

| Job | Interval | Purpose |
|---|---|---|
| `db_prefetch` | `PREFETCH_INTERVAL_MINUTES` (15) | RSS + Telegram sources -> new `NewsArticle` rows -> optional AI summary/title backfill |
| `scheduled_push` | 60 s | per-user digest (evening-only) or frequent-mode sends; skips quiet hours 00:00–06:00 Asia/Kuching |
| `db_cleanup` | `DB_CLEANUP_INTERVAL_HOURS` (24) | delete articles/deliveries older than `DB_RETENTION_DAYS` (30) |
| `ai_summary_watchdog` | startup + `AI_SUMMARY_ALERT_INTERVAL_HOURS` (6) | if recent new articles have zero AI summaries, send one private outage alert per incident to `AI_SUMMARY_ALERT_TELEGRAM_ID` |
| `utility_alert` | `UTILITY_ALERT_POLL_MINUTES` (10) | FYP2: official disruption sources -> classify -> store -> deliver to opted-in, location-matched users. LIVE since 2026-10-12 (`UTILITY_ALERT_SHADOW_MODE=false` in production; shadow flag remains for staging). Pushes are saved as `utility_alert` conversation turns so follow-ups ("how long will it last?") get deterministic answers from the notice |

### Delivery modes (per `UserPreference`)

- **digest** — one evening message (morning force-disabled by policy).
- **frequent** — timer-based (`every_15m` … `every_12h`); quiet when nothing new matches.
- **urgent alerts** — utility disruption alerts staged for opted-in users (`wants_urgent_alerts`).

### Commands

- User: `/start` (6-step onboarding), `/help`, `/latest`, `/settings`, `/setareas`, `/cancel`.
- Developer (`is_test_push_allowed`): `/testpush`, `/testdigestpush`, `/devwaze`, `/devutility` (FYP2 alert preview), `/devshadowlog` (FYP2 Stage 1b evidence summary from `utility_alert_shadow_log`), `/backfilltitles [limit]`, `/fetch`, `/deletedemo`.

### Conversational mode

Non-command private text -> `conversational_message`: pending area-keyword input, greetings,
"latest…"/"today summary", then news-agent Q&A (`services.get_news_agent_response_for_user`)
using RAG over recent articles (`RAG_NEWS_TOP_K` / `RAG_NEWS_CANDIDATE_POOL`) — answers from
the local DB only. Conversation memory (`conversation_service.py`, FYP2 doc 13) keeps recent
turns per user; short follow-ups ("how about Sibu?", "macam mana pula di Miri?") are expanded
into self-contained queries (EN/BM markers, topic/location swap, greeting stripping), and
article links already shown in the conversation are excluded from later answers. A
topic-relevance gate with synonym matching avoids false "no news" refusals; refusals suggest
real, recent follow-up questions.
Utility-alert pushes are also conversation turns (`interaction_type="utility_alert"` +
`utility_alert_id` on `conversation_messages`): a follow-up scoped to the notice ("how long
will it last?", "sudah pulih ke air?", "which areas are affected?") is answered
deterministically from the alert row (`utility_alert_service.answer_utility_alert_followup`)
instead of news retrieval — alerts are not news articles, so news Q&A cannot see them.
Alert recipients match `utility_alerts.locations` against user locations, and user
`area_keywords` against the notice text (street-level notices); street/landmark fragments
with no town name resolve via `SARAWAK_AREA_TO_TOWN` (location_extractor.py) instead of
broadcasting statewide.

## 4. Configuration catalog (`.env`, parsed in `src/core/config.py`)

- **Required:** `TELEGRAM_BOT_TOKEN`
- **Database:** `DATABASE_URL` (default `sqlite:///mvp.db`; Postgres URL in production)
- **Ollama:** `OLLAMA_API_BASE`, `OLLAMA_MODEL`, `OLLAMA_EMBED_MODEL`, `OLLAMA_API_KEY` +
  fallback chain (`OLLAMA_API_BASE_FALLBACK` / `OLLAMA_FALLBACK_API_KEY` / `OLLAMA_MODEL_FALLBACK`,
  `_2` variants); `OLLAMA_PRIMARY_TIMEOUT_SEC` (8) fails fast to fallbacks;
  `OLLAMA_KEEP_ALIVE` (default `30m`) keeps models resident between requests to avoid
  repeated 5–7 s reloads on local GPUs
- **Telegram sources (Telethon):** `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, and either
  `TELEGRAM_SESSION_STRING` (preferred on servers) or `TELEGRAM_PHONE` + session file
- **Toggles:** `PREFETCH_ENABLED`, `PREFETCH_AI_SUMMARY`, `DEDUPLICATION_ENABLED`,
  `CROSS_SOURCE_DEDUP_*`, `RAG_ENABLED`, quiet/digest hours, `DB_*`, `WAZE_*`, `TEST_PUSH_*`,
  `CONVERSATION_MEMORY_ENABLED` (default true; `CONVERSATION_MAX_TURNS` 3,
  `CONVERSATION_MAX_AGE_HOURS` 24 — see `docs/fyp2/13-conversation-memory.md`),
  `UTILITY_ALERT_*` (FYP2 — see `docs/fyp2/04-utility-alerts.md`)
- **AI-summary watchdog:** `AI_SUMMARY_ALERT_ENABLED` (default true),
  `AI_SUMMARY_ALERT_TELEGRAM_ID` (numeric private recipient; required to activate),
  `AI_SUMMARY_ALERT_WINDOW_HOURS` and `AI_SUMMARY_ALERT_INTERVAL_HOURS` (both default 6).
  No alert when the window has no new articles; one alert per outage until summaries recover.

`RSS_Sources.txt`: one URL per line; `#` comments; `telegram:@channel|session_name`.
New settings go through `config.py`, never scattered `os.getenv` calls.

To backfill today's missing summaries from a development machine using local Ollama only
(cloud fallbacks disabled), run `.venv\Scripts\python.exe scripts\backfill_local_summaries.py`.
Optional flags: `--model llama3.1` and `--limit N`.

**Feed fetch health (2026-10-14 audit, see `docs/fyp2/09-feed-source-audit.md`):**
RSS fetches use a browser-like Chrome User-Agent (`src/scrapers/rss_reader.py`) —
self-identifying bot UAs get 403 from some publishers. Three sources (See Hua, Borneo
Post, Free Malaysia Today) block Fly.io's datacenter IP range regardless of UA; they work
from local/dev networks only unless proxied. The dead rss.app feed is commented out in
`RSS_Sources.txt`.

**JBALB IP block (2026-11-11):** `jbalb.sarawak.gov.my` drops TCP connections from
datacenter ASNs (Fly.io connect-timeout since ~Nov 10; Cloudflare workers.dev returns
error 1010 for requests coming from Fly; public proxies time out too) but accepts
Malaysian residential/office connections. The relay worker in `tools/jbalb-relay` is a
documented dead end for this reason. Production workaround: `scripts/jbalb_local_relay.py`
(--once / --loop N / Task Scheduler) fetches JBALB from a non-blocked machine and inserts
notices through `utility_alert_service.ingest_raw_alerts` into the shared DB; the bot's
delivery job picks them up on its next cycle. The bot's own jbalb poll leg stays in place
and simply returns [] while the block persists.

## 5. Data model notes

- `User.telegram_id` is **BigInteger** — never shrink (32-bit overflow).
- `NewsArticle.last_sent_at` is **deprecated** (global dedup anti-pattern); per-user
  dedup uses `UserArticleDelivery` (and FYP2: `UtilityAlertDelivery`).
- `UtilityAlertShadowLog` (FYP2 Stage 1b): durable shadow-mode evidence — one `poll`
  row per detection cycle (JSON summary) + one deduped `would_deliver` row per
  (telegram_id, utility_alert_id) at first-due time. Written only while
  `UTILITY_ALERT_SHADOW_MODE=true`, gated by `UTILITY_ALERT_SHADOW_LOG`, purged with
  `DB_RETENTION_DAYS`. Would_deliver timestamps evidence quiet-hours holding and back
  the per-user rate-cap review. See `docs/fyp2/04-utility-alerts.md` §5.1.
- Schema changes: idempotent migration in `src/storage/migrate.py` + register in
  `init_db()`; must be safe to re-run at every startup.

## 6. Coding gotchas

- Absolute imports from project root (`from src.core...`); run from repo root; no `__main__` guards elsewhere.
- Blocking DB/HTTP/LLM work inside async handlers goes through `asyncio.to_thread(...)`.
- SQLite: WAL mode; handlers catch "database is locked" and ask the user to retry; long jobs (`/backfilltitles`) run as background `asyncio.Task`s.
- Telegram HTML parse mode for dynamic content — escape with `html.escape`; callback edits use `_safe_query_edit_message_text` to swallow "message is not modified".
- Dedup layers: (1) unique `NewsArticle.link`, (2) cross-source story matching, (3) per-user delivery records. Respect all three.
- Category taxonomy single-sourced in `news_categories.py`; location aliases live in BOTH `location_extractor.py` and `ai/summarizer.py` — keep in sync.
- Logging style is mostly `print()` to stdout (server log stream); `logging` is used sparingly. Match local style.
- Degrade gracefully when Ollama is unreachable (fall back to trimmed raw text / skip AI fields).
- Secrets (`.env`, `*.session`, session strings, `mvp.db`) never committed.
- Root `__pycache__` cleaned 2026-10-06; don't re-add stray caches.

## 7. Setup & run

```bash
python -m venv .venv && .venv\Scripts\activate     # Windows
pip install -r requirements.txt
ollama pull llama3.1 && ollama pull nomic-embed-text
# .env with TELEGRAM_BOT_TOKEN=...
python -m src.bot.bot_main
```

Telegram channel ingestion: `python scripts/test_sibuwb_bot.py` locally, log in, copy the
printed session string into `TELEGRAM_SESSION_STRING`.

FYP2 shadow logging: `powershell -ExecutionPolicy Bypass -File scripts/run_shadow_mode.ps1`
(tees console to `logs/utility_shadow_*.log`; alerts are detected but NOT pushed).
On servers, shadow evidence is also persisted to the `utility_alert_shadow_log` DB table
(survives log-stream rotation) — see `docs/fyp2/04-utility-alerts.md` §5.1.

Deploy: Docker build; on the server, Ollama `localhost` refers to the server, not your
laptop — use a cloud fallback or tunnel (`config.print_ollama_config_banner()` warns at startup).
