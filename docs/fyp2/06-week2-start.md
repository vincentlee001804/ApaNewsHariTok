# FYP 2 Progress Report — Week 2 start (2026-10-12)

**Project:** Apa News Hari Tok? (Telegram news bot for Sarawak local news)
**Author:** Vincent Lee Cheng Hong (BCS24020018)
**Period covered:** 2026-10-11 → 2026-10-12
**Status:** SV has **approved the milestone-aligned FYP2 Gantt chart** (delivered
in `Oct_Revised_FYP1_2 Gantt Chart.pdf`). First ~18 h of durable shadow evidence
collected on Fly.io. All implementation bars through W7 of the approved chart are
done or nearly done; software completion targeted by end of Week 2 (2026-10-18).
**Code:** `main` @ `be8ac2f` deployed as Fly release v113

> **Addendum (2026-10-16):** this report describes the state at Week 2 start, when
> utility alerts were still in shadow mode. The Stage 2 sign-off (doc 08) closed the
> same day, and the **go-live flip was executed 2026-10-12** — by the time of this
> addendum the module had delivered 934 real alerts to 52 users over 4 days
> (docs/fyp2/12-utility-alerts-go-live.md).

---

## 1. Gantt chart approved (2026-10-12)

Supervisor approved the revised FYP2 chart, now aligned to the official
Milestones Verification Form (W1 plan review, W3 dev in progress, W4 completed
implementation, W6 deployment, W7 test planning, W8 data collected, W11 Ch4
discussion, W12 final submission). Chart remains the *conservative plan*;
actual progress runs ahead of it (see §3).

## 2. Shadow-mode evidence — first ~18 hours on Fly.io (2026-10-11 13:43Z → 10-12)

Artifact: `utility_alert_shadow_log` table (Supabase) + `flyctl logs`.

- **Stability**: polls tick every 10 min (~17–21 s each), zero job errors, zero
  source failures, zero shadow-log write failures.
- **Telegram source watch item CLOSED**: `swbnews` is being fetched from Fly's
  datacenter IP (3 posts/poll in current window) — the session works from the
  server; no auth errors at any point.
- **2 new real alerts detected**: 10-11 13:20Z (117 would-be deliveries) and
  10-12 07:03Z (new JBALB/news alert, 132 would-be deliveries).
- **Quiet hours held**: the only would-deliver computation in the window landed
  at 15:03 Kuching time; nothing computed during 00:00–06:00 Asia/Kuching.
- **Cross-instance idempotency**: laptop → Fly handover deduped cleanly against
  the shared database.

Stage 1b criteria status: zero pushes ✅, stability ✅ (in progress), no
cross-poll duplicates ✅, quiet hours ✅ (no counter-example yet), precision /
rate-cap — pending a larger stored-alert sample.

## 3. Position vs the approved chart (start of Week 2)

| Approved bar (week) | Actual |
|---|---|
| Project Plan Review & Refinement (W1) | ✅ done (chart approved 10-12) |
| Database Infrastructure Migration (W2) | ✅ done 10-05 |
| Summary Length Consistency (W3) | ✅ done 10-05 |
| RAG & Hybrid Retrieval (W4) | ✅ done 10-06 |
| Semantic Dedup & Tuning Set (W4–5) | 🟡 core done 10-06; human-labeled tuning set open |
| Utility Alert Integration (W5) | ✅ **Stage 1 done 10-06; LIVE 10-12** (owner-approved after doc 08 sign-off; v120; clustering/threading remain as refinements) |
| NLP Benchmarking (W6–7) | ✅ done 10-12 (doc 07: llama3.1 local vs gpt-oss:20b cloud) |
| Deployment + Repository URL (W6–7) | ✅ done 10-11 (Fly v113, repo public) |

Everything scheduled through W7 is complete except the benchmark — roughly
3–5 weeks ahead of the approved chart.

## 4. Open items to close the software phase (target: 2026-10-18)

1. ~~**NLP benchmark (mimo)**~~ — **done 10-12 as doc 07**, with a scope correction:
   the cloud arm is **gpt-oss:20b** (the model the bot actually falls back to), not
   MiMo (fallback_2, invalid key). Four-dimension framework executed with automatic
   metrics + cross-model LLM judging. Production-relevant finding: the 384-token
   `OLLAMA_SUMMARY_NUM_PREDICT` cap truncates the reasoning model to empty output —
   raise it before relying on the cloud fallback.
2. ~~**Dedup human-labeled tuning set**~~ — **complete 10-12**: 40 owner-labeled pairs
   (`benchmark/dedup_tuning_set_20261012.xlsx`) + second-signal fix implemented and
   validated (doc 03 §3.3–3.4; labeled-set F1 0.541 → 0.692; production A/B clean).
3. **Utility-alert Stage 2 checklist** (04 §6): owner sign-off from shadow
   evidence, `/settings` toggle for `wants_urgent_alerts`, incident-level
   clustering, restoration threading, then `UTILITY_ALERT_SHADOW_MODE=false`.
4. **Hygiene**: Supabase password rotation (Fly holds `DATABASE_URL`),
   decision on RSS 403 mitigation (Fly IPs blocked by See Hua / Borneo Post /
   FMT / TVSarawak — prefetch either moves to the laptop or coverage is
   documented as a limitation).
5. Cosmetic: `logging.basicConfig(level=INFO)` so the service `NEW`-alert detail
   lines reach the log stream.

**Next report:** Week 3 entry expected to show benchmark results + tuning set +
Stage 2 decision, marking the software phase complete and shifting focus to the
survey instrument and report writing.

**Previous:** [05-fly-shadow-deployment](05-fly-shadow-deployment.md)
