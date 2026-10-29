# Utility Alerts — Stage 2 Go-Live Record

**Thesis link:** §5.4 limitation "Official utility alerts may arrive later than primary-source
announcements"; §5.5 future work #3 "real-time official alerts".
**Date recorded:** 2026-10-16 (FYP 2, Week 2)
**Purpose:** close the loop on doc 04 §5 / doc 08 — the Stage 2 go/no-go decision was taken
and executed on **2026-10-12**; this document records the live-delivery evidence accumulated
since. All numbers queried live from production Supabase on 2026-10-16.

---

## 1. Go-live decision

- **Decision:** flip `UTILITY_ALERT_SHADOW_MODE` from `true` to `false` (real delivery to
  opted-in users), owner-approved per the Stage 2 sign-off review in
  [08-utility-alerts-stage2-signoff](08-utility-alerts-stage2-signoff.md)
  (5/6 criteria pass; quiet-hours hold untested by data at the time).
- **Mechanism:** `UTILITY_ALERT_SHADOW_MODE = "false"` set in `[env]` of `fly.toml`
  (comment in file records the approval and date); no Fly secret overrides it — verified
  on the running machine with `printenv` (`false`).
- **First real delivery:** 2026-10-12 11:25 UTC — a few hours after the sign-off window
  closed, confirming the flip landed with the next deploy.

## 2. Live-delivery statistics (2026-10-12 → 2026-10-16, ~4 days)

| Metric | Value |
|---|---|
| Real deliveries sent (`utility_alert_delivery` rows) | **934** |
| Distinct users who received ≥ 1 alert | 52 |
| Distinct alerts delivered | 23 |
| Alerts stored (detection side) | 48 |
| Users opted out (`wants_urgent_alerts = false`) | 7 of 99 |
| Deliveries per day | 403 (10-12), 75, 162, 205, 89 (10-16, partial) |

### Reading the day-1 burst

The 403 deliveries on 2026-10-12 are the expected **go-live burst**: stored alerts whose
*event time* still fell inside the relevance window (`UTILITY_ALERT_DELIVER_PAST_HOURS`)
all became deliverable at once when shadow stopped swallowing them. This is the same
burst class the 2026-10-12 "go-live burst fix" anticipated (relevance window keyed on
`announced_at`, not storage time). Traffic then normalized to 75–205 deliveries/day.

## 3. Criterion closure vs doc 08

| # | Criterion (doc 08) | Status after go-live |
|---|---|---|
| 1 | Precision ≥ 90% | ✅ held pre-go-live (100% on 20-alert sample); no user complaints recorded in the window as a negative signal |
| 2 | Zero pushes (shadow) | n/a — superseded by real delivery (this doc) |
| 3 | Quiet-hours hold 00:00–06:00 Kuching | still the only untested path; overnight Sarawak alert announcements remain rare |
| 4 | Rate cap ≤ 3/h per user | ✅ same code path live; day-1 burst respects the cap by construction |
| 5 | Opt-out wired end-to-end | ✅ verified in code 2026-10-16: `_matching_user_ids()` filters `wants_urgent_alerts IS TRUE` at SQL level; 7 users have opted out and are excluded from all delivery computation |

## 4. What this evidence supports for the thesis

- **FYP2 future work #3 is now delivered, not just built** — official water/power disruption
  alerts are reaching real users (52 recipients in 4 days), with an opt-out mechanism
  (7 users) proving users can control it.
- The 48 stored / 23 delivered alert split shows the **location-matching + relevance-window
  filtering** working: roughly half of detections are either outside user locations or
  outside the delivery window — consistent with shadow-era precision.
- Remaining evaluation hooks for Phase 5: user response to alerts (retention of the toggle),
  and the first overnight announcement to close criterion 3.

## 5. Reproduction

```bash
# effective flag on the running machine
fly ssh console -a apanewsharitok -C "printenv UTILITY_ALERT_SHADOW_MODE"

# live counts (from the app machine, which holds DATABASE_URL)
fly ssh console -a apanewsharitok -C "python -c \"<sqlalchemy count queries>\""
```
