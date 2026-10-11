# FYP 2 Progress Report — Fly.io Shadow Deployment (2026-10-11, Week 2 start)

**Project:** Apa News Hari Tok? (Telegram news bot for Sarawak local news)
**Author:** Vincent Lee Cheng Hong (BCS24020018)
**Period covered:** 2026-10-11 (FYP 2, Week 2 start)
**Status:** Bot redeployed to Fly.io running current `main` in utility-alert
**shadow mode**; first server-side shadow polls verified. Main RSS prefetch
partially blocked by datacenter-IP filtering (documented below).
**Code:** `main` @ `176962e` deployed as Fly release v111

---

## 1. Why Fly.io (owner decision, 2026-10-11)

- Owner chose to keep the deployment on **Fly.io** (`apanewsharitok`,
  region `sin`) rather than Oracle Cloud "for now". Oracle container +
  GitHub-push auto-deploy remain paused and available as fallback.
- Driver: continuous multi-day shadow-log accumulation (Stage 1b of
  `04-utility-alerts.md`) without keeping the laptop on 24/7.
- **Ollama is not required** for this stage: the utility-alert classifier is
  rule-based and runs with Ollama down (04 §3.3, project rule #8). The Ollama
  cloud quota reset (due ~2026-10-12) only matters for optional AI enrichment.

## 2. Deployment actions

| Step | Result |
|---|---|
| flyctl v0.4.115 installed (workspace `tools/flyctl/`, portable) | ✅ |
| Auth: session had expired; owner re-authenticated (`bcs24020018@student.uts.edu.my`) | ✅ |
| `.dockerignore` hardened: added `*.session`, `tools`, `logs`, `*.pdf` | ✅ — the Telegram session file was previously baked into image layers; no longer |
| `flyctl deploy --app apanewsharitok --remote-only` | ✅ release v111 (previous image was from 10-06 09:22, predated Phase 4) |
| Secrets | Reused existing set: `TELEGRAM_BOT_TOKEN`, `DATABASE_URL` (Supabase pooler), `TELEGRAM_SESSION_STRING`, `TELEGRAM_API_ID/HASH`, source config. No `UTILITY_ALERT_*` overrides → code defaults apply (`SHADOW_MODE=true`, 10-min poll, 24h lookback, 3/h cap) |
| Machines | **Scaled 2 → 1.** Both machines polled Telegram with one token → `Conflict: terminated by other getUpdates request`. A polling bot must be single-instance; scaling to 1 also halves cost. (Prior 2-machine config predates this understanding.) |

## 3. Shadow-mode evidence (live, 2026-10-11 UTC)

```
09:34:06Z  [utility-alert] poll in 15.8s: {'fetched': 8, 'classified': 6, 'new': 0,
           'duplicates': 6, 'rejected': 2, 'sources': {'telegram': 0, 'jbalb': 6, 'news': 2}}
09:44:06Z  [utility-alert] poll in 15.7s: {'fetched': 8, 'classified': 6, 'new': 0,
           'duplicates': 6, 'rejected': 2, 'sources': {'telegram': 0, 'jbalb': 6, 'news': 2}}
```

- **Idempotency from a datacenter IP confirmed**: both polls stored 0 new rows
  and classified all 6 JBALB notices as duplicates of the 10-06 shadow run —
  cross-instance dedup via `source_ref` + title-Jaccard works against the
  shared Supabase database.
- **Zero pushes**: shadow mode only logs would-be deliveries.
- **Telegram source returned 0 twice with no auth errors** in the logs. Either
  no `swbnews` posts fell in the 24h lookback, or the session is silently
  struggling from Fly's IP. **Watch item** (see §5).
- No `Conflict` errors since the second machine was destroyed (09:29Z).

## 4. New finding: news-site anti-bot blocking from datacenter IPs

The regular RSS prefetch (separate from utility alerts) gets hard-blocked
from Fly's IP range:

```
09:43:09Z  Error fetching feed https://news.seehua.com/feed/: 403 Forbidden
09:43:09Z  Error fetching feed https://www.theborneopost.com/feed/: 403 Forbidden
09:43:14Z  Error fetching feed https://www.freemalaysiatoday.com/.../feed/: 403 Forbidden
09:43:16Z  Error fetching feed https://rss.app/feeds/EYJxxljrFQtbWR8K.xml: 402 Payment Required
09:43:21Z  Error fetching feed https://www.tvsarawak.my/feed/: 403 Forbidden
```

- See Hua / Borneo Post / FMT / TVSarawak block Fly.io IPs (Cloudflare-class
  bot protection). The utility-alert **Google News RSS watch is unaffected**
  (works from the same IP), so the alert pipeline is intact.
- The expired rss.app subscription (402) is pre-existing and unrelated to Fly.
- **Implication**: the main evening-digest article pipeline will starve on
  these sources while hosted on Fly. Options: (a) prefetch from the laptop /
  another residential-IP host, (b) accept reduced coverage and document it as
  an anti-bot limitation (strengthens thesis §5.4.5 source-coverage argument),
  (c) proxy. Deferred to owner decision — does not block Stage 1b.

## 5. Stage 1b acceptance-criteria tracking (from 04 §5)

| Criterion | Status 2026-10-11 |
|---|---|
| Precision ≥ 90% (sample ≥ 20 stored alerts) | Pending — needs multi-day accumulation; sample still the 10-06 set |
| Zero pushes | ✅ Verified (two polls, shadow mode on) |
| Quiet hours (00:00–06:00 held to 06:00 Asia/Kuching) | Pending — no overnight alert yet |
| Rate cap ≤ 3/h per user | Pending — no live alert yet |
| Stability (10-min ticks, non-fatal per-source errors) | ✅ Two consecutive on-schedule polls; per-source failure isolation verified 10-06 |
| No cross-poll duplicates | ✅ Verified across laptop → Fly instance boundary (stronger test than same-host) |

## 6. Open items / next

1. **Telegram source watch**: if `swbnews` posts within the next 24–48h and
   `telegram` count stays 0, investigate the session from Fly's IP before
   Stage 2 sign-off.
2. **Password rotation** (Supabase credential exposed 10-05) — still open;
   now also matters because the Fly app holds `DATABASE_URL`.
3. **~~Log durability~~ RESOLVED 2026-10-11 (commit `855f2f8`)**: every poll
   now writes a summary row and every first-due would-be delivery writes a
   deduped row to the new `utility_alert_shadow_log` table (Postgres/SQLite,
   gated by `UTILITY_ALERT_SHADOW_LOG`, shadow-mode only, retention-purged).
   Evidence survives log-stream rotation; the DB is the Stage 1b archive.
   Remaining: redeploy to Fly so the running instance ships evidence (the
   v111 image predates this change).
4. Owner decision: RSS 403 mitigation strategy (§4).
5. Local laptop bot must stay **off** while the Fly instance runs (same-token
   conflict).

**Previous:** [00-progress-report-20261006](00-progress-report-20261006.md) ·
[04-utility-alerts](04-utility-alerts.md)
