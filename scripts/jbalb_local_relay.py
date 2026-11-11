"""
Local JBALB relay — works around the .gov.my datacenter-ASN IP block.

Why: jbalb.sarawak.gov.my drops TCP connections from datacenter IP ranges
(Fly.io, Cloudflare edge invoked from Fly, public proxies — all connect
timeouts or CF error 1010), but accepts normal Malaysian residential/office
connections. The bot's Fly polls therefore cannot scrape JBALB; this script
fetches JBALB notices from THIS machine and inserts them through the bot's own
classification/dedup pipeline (utility_alert_service.ingest_raw_alerts), so the
rows land in the same utility_alerts table and the bot's delivery job picks them
up on its next cycle. Reruns are safe (source_ref + 48h title-Jaccard dedup).

Usage (from the repo root, with the venv active):
  .venv\\Scripts\\python.exe scripts\\jbalb_local_relay.py --once
  .venv\\Scripts\\python.exe scripts\\jbalb_local_relay.py --loop 30   # minutes

Scheduling (Task Scheduler, every 30 min while the machine is on):
  schtasks /create /tn "ApaNews JBALB relay" /tr "\"<repo>\\.venv\\Scripts\\python.exe\" \"<repo>\\scripts\\jbalb_local_relay.py\" --once" /sc minute /mo 30

Env: uses .env by default; pass --env .env.production to target the production
(Supabase) database — the same DATABASE_URL the deployed bot uses.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv  # noqa: E402


def one_cycle() -> dict:
    from src.core.config import UTILITY_ALERT_JBALB_URLS
    from src.core.utility_alert_service import ingest_raw_alerts
    from src.scrapers.utility_alert_reader import fetch_jbalb_notices

    raw = [a for url in UTILITY_ALERT_JBALB_URLS for a in fetch_jbalb_notices(url)]
    summary = ingest_raw_alerts(raw)
    summary["fetched"] = len(raw)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--env",
        default=".env",
        help="env file with DATABASE_URL (default: .env; use .env.production for prod)",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="run one fetch/ingest cycle")
    mode.add_argument(
        "--loop",
        type=int,
        metavar="MINUTES",
        help="run forever, one cycle every MINUTES (Ctrl+C to stop)",
    )
    args = parser.parse_args()

    env_file = PROJECT_ROOT / args.env
    if env_file.exists():
        load_dotenv(env_file, override=True)
        print(f"[jbalb-relay] loaded env from {env_file}", flush=True)

    if args.loop:
        print(f"[jbalb-relay] looping every {args.loop} min; Ctrl+C to stop", flush=True)
        while True:
            _run_cycle()
            time.sleep(max(1, args.loop) * 60)
    else:
        _run_cycle()


def _run_cycle() -> None:
    started = datetime.utcnow()
    try:
        summary = one_cycle()
        print(
            f"[jbalb-relay] {datetime.utcnow().isoformat(timespec='seconds')}Z "
            f"cycle in {(datetime.utcnow() - started).total_seconds():.1f}s: {summary}",
            flush=True,
        )
    except Exception as e:  # noqa: BLE001 — a failed cycle must not kill the loop
        print(f"[jbalb-relay] cycle failed: {e}", flush=True)


if __name__ == "__main__":
    main()
