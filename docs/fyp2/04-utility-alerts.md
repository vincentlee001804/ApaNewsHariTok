# Utility Disruption Notification Module (Stage 1 — Shadow Mode)

**Thesis link:** §5.4 limitation "Official utility alerts may arrive later than primary-source
announcements"; §5.5 future work #3 "real-time official alerts".
**Date:** 2026-10-06 (FYP 2, Week 1)
**Status:** Stage 1 complete — detection, classification, dedup and would-be delivery
computation run live in **shadow mode** (nothing is pushed to users). Stage 2 (enabling
pushes) requires owner sign-off after shadow review.

---

## 1. Problem statement

Users currently learn about water cuts and power outages from the normal news pipeline
(15-minute RSS prefetch + evening digests), or not at all. There is no dedicated,
fast path for official utility disruption notices — and the FYP 1 evaluation recorded
that closed official platforms (Facebook-first agencies) make these alerts arrive late.

## 2. Source scouting (2026-10-06) — including negative results

| Source | Region | Channel | Decision |
|---|---|---|---|
| Sarawak Water (merged KWB/SWB/LAKU, Aug 2025) | Central (Sibu) | **Official Telegram channel `t.me/swbnews`** (6.2K subs, structured notices, several/day) | ✅ Integrated via existing session reader — near-real-time, free |
| JBALB (Rural Water Supply Dept) | Rural statewide | **Official .gov.my announcement list** — notices dated same-day | ✅ Integrated — direct scrape, polite (1.5 s between detail fetches) |
| Google News RSS watches | All Sarawak (esp. South/North + power) | Free keyword watches | ✅ Integrated — 15–60 min lag behind official posts |
| Sarawak Water South/North regions | Kuching/Miri | Facebook pages only | ❌ No public Telegram channel found (probed `swsbsouth`, `kwbnews`, `sarawakwater`); FB bridge = optional rss.app subscription (future, ~US$9/mo) |
| Sarawak Energy | Power | Facebook + X + SEB Cares app | ❌ X API is paid (owner-verified) → documented negative result; FB app review impractical for FYP. Covered indirectly via Google News RSS + website media releases |

The X API cost and the Meta app-review wall are the recorded "attempt" for thesis §5.5
#3 — "integrate **where feasible**"; the feasible routes are what shipped.

## 3. Changes made

### 3.1 Config (`src/core/config.py`)

| Variable | Default | Purpose |
|---|---|---|
| `UTILITY_ALERT_ENABLED` | `true` | Master switch for the poll job. |
| `UTILITY_ALERT_SHADOW_MODE` | `true` | **Stage 1: detect + log only, no pushes.** `false` = live delivery. |
| `UTILITY_ALERT_POLL_MINUTES` | `10` | Poll cadence. |
| `UTILITY_ALERT_LOOKBACK_HOURS` | `24` | How far back telegram/news sources look. |
| `UTILITY_ALERT_MAX_PER_HOUR_PER_USER` | `3` | Per-user rate cap. |
| `UTILITY_ALERT_TELEGRAM_CHANNELS` | `swbnews` | Official channel(s) via existing session reader. |
| `UTILITY_ALERT_JBALB_URLS` | JBALB announcement list | .gov.my scrape targets. |
| `UTILITY_ALERT_NEWS_QUERIES` | water disruption / pipe burst / power outage | Google News RSS watches (server-side `when:7d` recency operator). |

Quiet hours: alerts reuse the scheduled-push window (12am–6am `Asia/Kuching`) — held
until 06:00 (owner decision 2026-10-06). Opt-in: `user_preferences.wants_urgent_alerts`
(default on; `/settings` toggle ships with Stage 2).

### 3.2 Storage

- `utility_alerts` — source, source_ref (unique per source), title, text, utility_type
  (water/power/other), kind (scheduled/unscheduled/restored/info), canonical locations,
  raw affected-area snippet, announced_at, window, status. Plain columns → works on
  Postgres **and** SQLite; idempotent metadata-create migration.
- `utility_alert_delivery` — per-user delivery records (same pattern as
  `UserArticleDelivery`; no global "already sent" flag).
- Retention: rows older than `DB_RETENTION_DAYS` are purged by the poll cycle.

### 3.3 Detection + classification (`src/scrapers/utility_alert_reader.py`)

- **Rule-based classifier** (English + Bahasa Malaysia keyword sets, word-boundary
  matching) — runs with Ollama down (rule #8). kind precedence: restored > scheduled >
  unscheduled; `"scheduled"` cannot fire inside `"unscheduled"` (word boundaries).
- Location extraction reuses the shared `SARAWAK_LOCATION_ALIASES` taxonomy (rule #4).
- JBALB: list page → matching notice links → detail pages (bodies load via AJAX, so the
  stored text is title-carried; boilerplate stripped).
- News watch: Google News RSS ranks plain `q=` by relevance (months-old hits), so the
  fetcher appends `when:7d` server-side + a client-side 48 h age cap, and requires a
  Sarawak signal ("sarawak" in text or a canonical location) — rejects e.g. Myanmar
  El Niño outage news.

### 3.4 Service + wiring

- `src/core/utility_alert_service.py` — `poll_utility_alerts()` (fetch → classify →
  dedup → insert → log; per-source failures are counted, never fatal),
  `compute_pending_deliveries()` (location matching, quiet hours, rate cap, per-user
  delivery skip), `format_utility_alert_html()` (HTML-escaped per rule #7).
- Dedup: exact `source_ref` + title-Jaccard (48 h lookback). Title-only tokens after
  finding JBALB's AJAX boilerplate made every notice look identical.
- `bot_main.py`: repeating job at `UTILITY_ALERT_POLL_MINUTES` (blocking work in
  `asyncio.to_thread`, rule #6); sends only when shadow mode is off.
- `/devutility` developer preview (same allow-list as `/devwaze`).

## 4. Shadow-mode evaluation (2026-10-06, live sources)

Artifact: `benchmark/utility_alerts_shadow_20261006.txt`.

```
poll 1: fetched 16 (telegram 4, jbalb 8, news 4) → classified 10 → new 10
poll 2: new 0, duplicates 10   (idempotent re-poll — nothing double-inserted)
stored: jbalb 8 (7 locations: Betong ×2, Bintulu, Sarikei ×2, Serian, Sibu/Selangau,
        Samarahan), telegram 2 (Sibu pipe works, unscheduled notice)
rejected 6: Myanmar El Niño outage (no Sarawak signal), 2 non-notice channel posts,
        3 off-topic news items
pending deliveries computed: 155 (53 opted-in users × 3/h cap on a fresh table —
steady state tracks actual new alerts)
```

Dedup/idempotency verified (second poll stores nothing); per-source failure handling
verified live when JBALB briefly throttled repeated debug fetches (warnings logged,
cycle continued — the politeness delay was added after).

## 5. Stage 2 checklist (go-live, requires owner approval)

1. Watch shadow logs for several days (`/devutility` + `[utility-alert]` log lines).
2. Add `/settings` toggle for `wants_urgent_alerts` (column already exists).
3. Incident-level clustering for the news watch (multiple outlets report the same burst;
   reuse Phase 3 semantics) so users get one alert per incident, not per story.
4. Link "repair completed" follow-ups to their original notice (thread context) for
   restoration notifications.
5. Optional: rss.app bridge on Sarawak Water South FB page for near-real-time Kuching
   coverage (paid).
6. Set `UTILITY_ALERT_SHADOW_MODE=false` and redeploy.

## 6. Threats to validity / risks

1. Google News watch latency (15–60 min) is out of our control; Telegram/JBALB are
   near-real-time but cover Central + rural respectively. South/North water and power
   stay news-mediated until an official channel appears (or rss.app is subscribed).
2. Rule-based classification can miss novel phrasings; shadow logs are the tuning set.
   LLM enrichment is future work (must degrade gracefully, rule #8).
3. JBALB detail bodies are AJAX-loaded — if their CMS changes, titles still carry
   location/date, but affected-area lists will be absent until the scraper is adjusted.
4. False-positive alerts erode trust quickly — shadow mode exists exactly to measure
   precision before users see anything.
