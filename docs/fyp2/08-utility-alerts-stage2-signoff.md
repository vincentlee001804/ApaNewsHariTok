# Utility Alerts — Stage 2 Sign-off Summary (Shadow Evidence Review)

**Thesis link:** §5.4 limitation "Official utility alerts may arrive later than primary-source
announcements"; §5.5 future work #3 "real-time official alerts".
**Date:** 2026-10-12 (FYP 2, Week 2)
**Purpose:** the owner-facing evidence pack for the Stage 2 go/no-go decision (doc 04 §6).
All numbers below are queried live from the production Supabase `utility_alert_shadow_log`
/ `utility_alerts` / `utility_alert_delivery` tables (deploy v116, shadow mode on).

---

## 1. Evidence window

- Shadow tables active **2026-10-11 13:10 UTC → 2026-10-12 10:47 UTC** (~21.6 h,
  132 poll cycles). The earlier 10-06 shadow evaluation (doc 04 §4) covers detection
  correctness; this window covers the durable per-cycle evidence.

## 2. Acceptance criteria verdicts (doc 04 §5)

| # | Criterion | Verdict | Evidence |
|---|---|---|---|
| 1 | Precision ≥ 90% (≥20 stored alerts) | ✅ **100% on sample (20/20)** | all stored rows are genuine Sarawak **water** disruption notices: 16 JBALB .gov.my (14 distinct locations statewide) + 4 official `swbnews` Telegram. See §4 for the review list. Caveat: power/South-North water remain news-mediated (doc 04 §2) — zero such alerts stored in this window, so precision *for those sources is untested*. |
| 2 | Zero pushes | ✅ | `utility_alert_delivery` row count = **0** while shadow mode on. |
| 3 | Quiet hours hold (00:00–06:00 Kuching) | 🟡 **mechanism present, untested by data** | no alert in the window was announced inside quiet hours, so no hold was exercised. The hold path exists in `compute_pending_deliveries`; first overnight announcement will produce direct evidence (would_deliver `created_at` local ≥ 06:00). |
| 4 | Rate cap ≤ 3/h per user | ✅ | max would-deliver events per user-hour = **3** (= cap), across 145 would-deliver rows / 53 users. |
| 5 | Stability — tick every 10 min, errors rare/non-fatal | ✅ | 132 consecutive ticks, gap median **10.0 min, max 10.0 min**, zero gaps; poll payloads report **0 errors**; per-source failure isolation verified earlier (doc 04 §4). |
| 6 | No cross-poll duplicates | ✅ | 829 duplicate detections swallowed across 132 cycles — re-polls stored nothing new (idempotent `source_ref` + 48 h title-Jaccard). |

**Overall: 5/6 criteria pass, 1 untested-by-circumstance (no overnight announcement yet).**
No criterion failed.

## 3. Detection volume in window

- 20 stored alerts (new since 10-03): 16 JBALB + 4 Telegram; 14 unscheduled / 6
  scheduled; **all water**; locations: Betong ×4, Samarahan/Asajaya ×2, Serian ×2,
  Sarikei ×2, Sibu/Selangau, Sri Aman, Kapit/Song, Mukah/Dalat, Bintulu/Sebauh +
  2 Telegram Sibu notices.
- 145 would-deliver computations (53 opted-in users × matched alerts, first-due only).

## 4. Stored alerts for the precision spot-check (owner review)

Dates are announcement time (Asia/Kuching). All titles are official interruption
notices; no off-topic or non-Sarawak content in the sample.

| Source | Kind | Announced | Title (truncated) |
|---|---|---|---|
| jbalb | scheduled | 10-16 08:00 | Scheduled Water Interruption Notice at Serian (Gedong) |
| telegram | scheduled | 10-12 16:08 | Scheduled Water Supply Interruption Notice |
| telegram | unscheduled | 10-12 14:59 | Unscheduled Water Supply Interruption |
| jbalb | scheduled | 10-12 08:00 | Scheduled Water Interruption Notice at Samarahan (Asajaya) |
| jbalb | unscheduled | 10-10 08:00 | Unscheduled Water Interruption Notice at Betong (Spaoh, Debak) |
| jbalb | unscheduled | 10-10 08:00 | Unscheduled Water Interruption Notice at Samarahan (Sebuyau) |
| jbalb | unscheduled | 10-10 08:00 | Unscheduled Water Interruption Notice at Kapit (Song) |
| jbalb | scheduled | 10-09 08:00 | Scheduled Water Interruption Notice at Serian |
| jbalb | unscheduled | 10-09 08:00 | Unscheduled Water Interruption Notice at Sri Aman |
| jbalb | unscheduled | 10-09 08:00 | Unscheduled Water Interruption Notice at Betong (Saratok, Pusa, Debak) |
| jbalb | unscheduled | 10-09 08:00 | Unscheduled Water Interruption Notice at Mukah (Dalat) |
| jbalb | scheduled | 10-08 08:00 | Scheduled Water Interruption Notice at Sarikei |
| telegram | unscheduled | 10-06 15:07 | Unscheduled Water Supply Interruption |
| telegram | unscheduled | 10-06 09:20 | Interconnection of 12" AC Pipe at Jalan Desa… |
| jbalb | unscheduled | 10-05 08:00 | Unscheduled Water Interruption Notice at Sibu (Selangau) |
| jbalb | unscheduled | 10-05 08:00 | Unscheduled Water Interruption Notice at Betong (Spaoh, Debak) |
| jbalb | scheduled | 10-05 08:00 | Scheduled Water Interruption Notice at Bintulu (Sebauh) |
| jbalb | unscheduled | 10-05 08:00 | Unscheduled Water Interruption Notice at Betong (Kabong, Roban) |
| jbalb | unscheduled | 10-04 08:00 | Unscheduled Water Interruption Notice at Sarikei (Meradong) |
| jbalb | unscheduled | 10-03 08:00 | Unscheduled Water Interruption Notice at Samarahan (Asajaya) |

## 5. Stage 2 checklist status (doc 04 §6)

| # | Item | Status |
|---|---|---|
| 1 | Shadow evidence review | ✅ this document (owner to confirm) |
| 2 | `/settings` toggle for `wants_urgent_alerts` | ✅ **already live** — correction to doc 04 §6: the `/settings` "🚨 Urgent Alerts: ON/OFF" toggle and its callback (`settings_toggle_urgent`) shipped earlier and the delivery path filters on it (`_matching_user_ids` requires `wants_urgent_alerts.is_(True)`). Verified in code 10-12. |
| 3 | Incident clustering for news watch | ⬜ open (prevents duplicate alerts when multiple outlets cover one burst) |
| 4 | Restoration threading | ⬜ open (link "restored" follow-ups to the original notice) |
| 5 | rss.app FB bridge (optional, paid) | ⬜ owner decision deferred |
| 6 | `UTILITY_ALERT_SHADOW_MODE=false` + redeploy | ✅ **done 2026-10-12** — owner-approved; live since 19:25 Asia/Kuching (Fly v120). First live cycle: 142 deliveries recorded; one dead test account (123456) failed gracefully ("Chat not found", logged, non-fatal). |

## 6. Recommendation

**Executed 2026-10-12:** item 6 flipped after owner approval; the module is live (see
the status table). Post-go-live refinements remain: items 3–4 (clustering, restoration
threading) and the optional item 5 — the known cost of going live without item 3 is
that a burst covered by several outlets may arrive as multiple alerts (rate-capped at
3/user/h).
