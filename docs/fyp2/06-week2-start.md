# FYP 2 Progress Report — Week 2 start (2026-10-12)

**Project:** Apa News Hari Tok? (Telegram news bot for Sarawak local news)
**Author:** Vincent Lee Cheng Hong (BCS24020018)
**Period covered:** 2026-10-11 → 2026-10-12
**Status:** SV has **approved the milestone-aligned FYP2 Gantt chart** (delivered
in `Oct_Revised_FYP1_2 Gantt Chart.pdf`). First ~18 h of durable shadow evidence
collected on Fly.io. All implementation bars through W7 of the approved chart are
done or nearly done; software completion targeted by end of Week 2 (2026-10-18).
**Code:** `main` @ `be8ac2f` deployed as Fly release v113

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
| Utility Alert Integration (W5) | 🟡 Stage 1 done 10-06; Stage 2 pending owner sign-off |
| NLP Benchmarking (W6–7) | ⬜ not started (Ollama quota reset 10-12) |
| Deployment + Repository URL (W6–7) | ✅ done 10-11 (Fly v113, repo public) |

Everything scheduled through W7 is complete except the benchmark — roughly
3–5 weeks ahead of the approved chart.

## 4. Open items to close the software phase (target: 2026-10-18)

1. **NLP benchmark (mimo)** — the deferred Phase 1 production-model run over the
   four dimensions (relevance, coherence, factual consistency, compression);
   Ollama cloud quota resets today.
2. **Dedup human-labeled tuning set** — label ~a few dozen candidate pairs to
   validate/retune the 0.85 cosine threshold (was derived from 249 labeled pairs;
   final tuning set still owed per 00 §5).
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
