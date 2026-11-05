"""Fill today's missing article summaries with local Ollama only."""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def _ollama_tags(base_url: str, timeout: float = 5.0) -> dict | None:
    try:
        response = requests.get(f"{base_url}/api/tags", timeout=timeout)
        response.raise_for_status()
        return response.json()
    except Exception:
        return None


def _model_is_available(tags: dict | None, model: str) -> bool:
    if not tags or not isinstance(tags, dict):
        return False
    target = model.split(":")[0].strip().lower()
    for entry in tags.get("models", []):
        name = (entry.get("name") or "").split(":")[0].strip().lower()
        if name == target:
            return True
    return False


def _start_ollama_server(base_url: str, max_wait_sec: int = 30) -> bool:
    ollama_bin = shutil.which("ollama")
    if not ollama_bin:
        return False
    try:
        kwargs: dict = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        subprocess.Popen(
            [ollama_bin, "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **kwargs,
        )
    except Exception:
        return False

    deadline = time.time() + max_wait_sec
    while time.time() < deadline:
        time.sleep(1)
        if _ollama_tags(base_url, timeout=2.0) is not None:
            return True
    return False


def ensure_ollama_ready(base_url: str, model: str) -> None:
    tags = _ollama_tags(base_url)
    if tags is not None:
        if _model_is_available(tags, model):
            print(f"Ollama ready: {model} at {base_url}")
            return
        print(f"Ollama server is running, but model '{model}' is not pulled locally.")
        print(f"Run: ollama pull {model}")
        raise SystemExit(1)

    print(f"Ollama not reachable at {base_url}. Attempting to start...")
    if _start_ollama_server(base_url):
        tags = _ollama_tags(base_url)
        if _model_is_available(tags, model):
            print(f"Ollama ready: {model} at {base_url}")
            return
        print(f"Ollama started, but model '{model}' is not pulled locally.")
        print(f"Run: ollama pull {model}")
        raise SystemExit(1)

    print("Could not start Ollama. Please start it manually with: ollama serve")
    raise SystemExit(1)


def main() -> None:
    # Avoid UnicodeEncodeError on Windows consoles when printing non-ASCII headlines.
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    parser = argparse.ArgumentParser(
        description="Backfill recent missing AI summaries using localhost Ollama."
    )
    parser.add_argument("--model", default="llama3.1", help="Local Ollama model")
    parser.add_argument(
        "--hours",
        type=int,
        default=0,
        help=(
            "Look back this many hours from now for articles to backfill. "
            "If 0 (default), only backfill articles created since midnight UTC today."
        ),
    )
    parser.add_argument(
        "--limit", type=int, default=0, help="Maximum articles; 0 means all"
    )
    parser.add_argument(
        "--force", action="store_true", help="Regenerate existing summaries/titles"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="List articles without calling Ollama"
    )
    parser.add_argument(
        "--verbose", action="store_true", help="Print per-article details"
    )
    args = parser.parse_args()
    if args.limit < 0:
        parser.error("--limit must be 0 or greater")
    if args.hours < 0:
        parser.error("--hours must be 0 or greater")

    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    if args.verbose:
        logging.getLogger("src.core.services").setLevel(logging.INFO)

    load_dotenv(".env")
    os.environ["OLLAMA_API_BASE"] = "http://localhost:11434"
    os.environ["OLLAMA_MODEL"] = args.model
    os.environ["OLLAMA_API_BASE_FALLBACK"] = ""
    os.environ["OLLAMA_API_BASE_FALLBACK_2"] = ""
    os.environ["OLLAMA_MODEL_FALLBACK_2"] = ""

    base_url = "http://localhost:11434"

    from datetime import datetime, timedelta

    from sqlalchemy import select

    from src.core.models import NewsArticle
    from src.core.services import backfill_ai_summaries_for_article_ids
    from src.storage.database import SessionLocal

    now = datetime.utcnow()
    if args.hours > 0:
        start = now - timedelta(hours=args.hours)
        window_label = f"the last {args.hours} hour(s)"
    else:
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        window_label = "today"

    missing = (NewsArticle.ai_summary.is_(None)) | (NewsArticle.ai_summary == "")
    with SessionLocal() as session:
        stmt = (
            select(NewsArticle.id, NewsArticle.title, NewsArticle.link)
            .where(NewsArticle.created_at >= start, missing)
            .order_by(NewsArticle.created_at.desc())
        )
        if args.limit:
            stmt = stmt.limit(args.limit)
        rows = list(session.execute(stmt).all())

    total = len(rows)
    print(
        f"Backfilling {total} article(s) from {window_label} with local {args.model}; cloud fallback disabled."
    )

    if args.dry_run:
        for aid, title, link in rows:
            print(f"  [dry-run] id={aid} | {title[:80]} | {link[:60]}")
        print(f"Dry run complete. {total} article(s) would be processed.")
        return

    ensure_ollama_ready(base_url, args.model)

    ids = [aid for aid, _, _ in rows]
    batch_size = 5

    total_saved = 0
    total_failed = 0
    total_skipped = 0

    total_batches = (len(ids) + batch_size - 1) // batch_size
    for offset in range(0, len(ids), batch_size):
        batch = ids[offset : offset + batch_size]
        result = backfill_ai_summaries_for_article_ids(
            batch, force=args.force, verbose=args.verbose
        )
        total_saved += result.saved
        total_failed += result.failed
        total_skipped += result.skipped
        batch_num = offset // batch_size + 1
        print(
            f"Batch {batch_num}/{total_batches}: saved {result.saved} | failed {result.failed} | skipped {result.skipped}",
            flush=True,
        )

    with SessionLocal() as session:
        remaining = len(
            list(
                session.execute(
                    select(NewsArticle.id).where(
                        NewsArticle.created_at >= start, missing
                    )
                )
                .scalars()
                .all()
            )
        )
    print(
        f"Finished. Saved {total_saved}, failed {total_failed}, skipped {total_skipped}. "
        f"Articles from {window_label} still without summaries: {remaining}."
    )


if __name__ == "__main__":
    main()
