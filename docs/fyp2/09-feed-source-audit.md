# 09 — Feed Source Audit & Fetch Fix (2026-10-14)

**Motivation.** Production (Fly.io, Singapore) was silently fetching fewer news sources than
configured. A live audit compared what `RSS_Sources.txt` promises against what actually
returns news, both from the developer machine and from inside the Fly machine.

## Audit method

1. Parsed `RSS_Sources.txt` (18 active URLs; 2 commented-out Telegram entries).
2. Fetched every URL from the local machine with a browser User-Agent (script kept at
   `scripts/check_feeds.py`).
3. Read the live Fly logs (`flyctl logs -a apanewsharitok`) for recurring prefetch errors.
4. Re-tested the four 403 feeds **from inside the Fly machine** (`flyctl ssh console`)
   with a real Chrome UA to separate UA-blocking from IP-blocking.

## Results

| # | Source | Local (browser UA) | Fly (bot UA) | Fly (browser UA) | Cause |
|---|--------|--------------------|--------------|--------------------|-------|
| 1 | sarawaktribune.com | ✅ 10 items | ✅ | — | — |
| 2 | news.seehua.com | ✅ 30 items | ❌ 403 | ❌ 403 | Datacenter-IP block |
| 3 | theborneopost.com | ✅ 20 items | ❌ 403 | ❌ 403 | Datacenter-IP block |
| 4 | malaysiakini.com | ✅ 10 items | ✅ | — | — |
| 5 | malaymail.com | ✅ 50 items | ✅ | — | — |
| 6 | bernama.com | ✅ 10 items | ✅ | — | — |
| 7 | lowyat.net | ✅ 10 items | ✅ | — | — |
| 8 | freemalaysiatoday.com | ✅ 50 items | ❌ 403 | ❌ 403 | Datacenter-IP block |
| 9 | hmetro.com.my/utama.xml | ✅ 30 items | ✅ | — | — |
| 10 | hmetro.com.my/mutakhir.xml | ✅ 20 items | ✅ | — | — |
| 11 | dayakdaily.com | ✅ 10 items | ✅ | — | — |
| 12 | rss.app feed | ❌ 402 Payment Required | ❌ 402 | — | Lapsed paid subscription |
| 13 | sarawakedition.com | ✅ 10 items | ✅ | — | — |
| 14 | cms.buletintv3.my | ✅ 10 items | ✅ | — | — |
| 15 | berita.rtm.gov.my | ✅ 10 items | ✅ | — | — |
| 16 | news.google.com "Sarawak water" | ✅ 100 items | ✅ | — | — |
| 17 | news.google.com "Sarawak energy" | ✅ 100 items | ✅ | — | — |
| 18 | tvsarawak.my | ✅ 10 items | ❌ 403 | ✅ 200 | Bot User-Agent block |

**Tally:** 18 configured → 17 working locally → **13 working on Fly** before the fix.

Note: `seehua` and `buletintv3` return slightly malformed XML (leading whitespace /
unbound prefix), but `feedparser` tolerates both — no code change needed.

## Changes made (2026-10-14)

1. **`src/scrapers/rss_reader.py`** — the fetch User-Agent changed from the self-identifying
   `Mozilla/5.0 (ApaNewsHariTok bot)` to a real Chrome 129 UA plus an explicit
   `Accept: application/rss+xml, ...` header. Evidence: with this UA, TV Sarawak returns
   200 from the Fly machine (was 403 on every 20-minute prefetch cycle).
2. **`RSS_Sources.txt`** — the dead `https://rss.app/feeds/EYJxxljrFQtbWR8K.xml` line is
   commented out (402 Payment Required: paid subscription lapsed).

After redeploy, production should fetch **14 of 17 remaining sources**.

## Known issue — remaining 3 sources (datacenter-IP blocked)

See Hua, Borneo Post, and Free Malaysia Today block Fly.io's IP range at the CDN/WAF
level; no header change helps (verified from inside the Fly machine). Options, in order of
preference for a future phase:

1. Route only these feeds through a feed-proxy (self-hosted RSSHub, or a small proxy
   worker) so the egress IP is residential/cloud-generic.
2. Replace with publisher-approved APIs where offered.
3. Accept the loss: these 3 remain available when the bot runs from a non-datacenter
   network (e.g. local dev).

Do not attempt UA rotation/cookies for these — the block is IP-based, not UA-based.

## 2026-10-14 additions (same day)

Source-expansion sweep: 25+ candidates tested locally, then re-verified **from inside the
Fly machine** so production behavior is known in advance. Added to `RSS_Sources.txt`:

| Source | Feed URL | Items/cycle | Rationale |
|--------|----------|-------------|-----------|
| Astro Awani | `https://www.astroawani.com/rss.xml` | 10 | Strong Sarawak coverage, national TV news |
| Berita Harian | `https://www.bharian.com.my/feed` | 50 | Major Malay daily; complements Harian Metro |
| The Sun Daily | `https://www.thesun.my/rss` | 10 | National English, low load |

Verified-but-not-added (kept in reserve; The Vibes and Malaysian Insight return 100
items/cycle each, which is heavy for a 1 GB machine):
`https://www.thevibes.com/rss`, `https://www.nst.com.my/feed`,
`https://www.themalaysianinsight.com/feed/`, `https://focusmalaysia.my/feed/`,
`https://www.therakyatpost.com/feed/`, `https://www.malaysianow.com/feed/`

**No public RSS (rejected):** Utusan Borneo (biggest Sarawak gap), Sin Chew, China Press,
Oriental Daily, Sinar Harian, The Star, The Edge, official Sarawak gov / UKAS portals.
These would need an RSSHub/proxy route (same solution as the 3 IP-blocked sources).

**Infra:** `fly.toml` VM memory raised 512 MB → 1 GB (shared-cpu-1x) to head off memory
pressure from the wider source list; deployed 2026-10-14.

## Reproduce

```powershell
python scripts/check_feeds.py                      # local check of every source
flyctl logs -a apanewsharitok                      # live prefetch errors
flyctl ssh console -a apanewsharitok -C "python -c \"...\""   # fetch from Fly's network
```

**Deployment note:** requires `fly deploy` for both changes to reach production
(`RSS_Sources.txt` is baked into the image via `COPY . .`).
