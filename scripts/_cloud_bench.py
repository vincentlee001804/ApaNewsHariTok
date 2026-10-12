"""Temp helper: run the summary benchmark against the configured cloud fallback
(Ollama Cloud) without printing any credential material.

Usage:
  python scripts/_cloud_bench.py probe                      # one tiny request, print status only
  python scripts/_cloud_bench.py run -- <benchmark args>    # e.g. run -- --models gpt-oss:20b --label gptoss_v2 ...
"""
from __future__ import annotations

import importlib
import os
import runpy
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _point_env_at_cloud() -> None:
    import src.core.config as cfg

    if not cfg.OLLAMA_API_BASE_FALLBACK or not cfg.OLLAMA_FALLBACK_API_KEY:
        raise SystemExit("cloud fallback base/key not configured in .env")
    os.environ["OLLAMA_API_BASE"] = cfg.OLLAMA_API_BASE_FALLBACK
    os.environ["OLLAMA_API_KEY"] = cfg.OLLAMA_FALLBACK_API_KEY
    # Benchmark-only: gpt-oss is a reasoning model and burns the production 384-token
    # budget on hidden reasoning, returning empty output (done_reason=length). Give the
    # benchmark headroom; production config stays untouched.
    os.environ.setdefault("OLLAMA_SUMMARY_NUM_PREDICT", "1024")
    importlib.reload(cfg)


def probe() -> None:
    import requests

    import src.core.config as cfg

    _point_env_at_cloud()
    url = cfg.OLLAMA_GENERATE_URL
    model = sys.argv[2] if len(sys.argv) > 2 else cfg.OLLAMA_MODEL_FALLBACK
    r = requests.post(
        url,
        json={"model": model, "messages": [{"role": "user", "content": "Reply with: OK"}], "stream": False},
        headers=cfg.ollama_request_headers(),
        timeout=120,
    )
    print("url:", url)
    print("model:", model)
    print("status:", r.status_code)
    if r.status_code != 200:
        print("body:", r.text[:400])
    else:
        data = r.json()
        msg = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        print("reply:", (msg or "")[:100])


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "probe"
    if mode == "probe":
        probe()
    elif mode == "run":
        _point_env_at_cloud()
        sys.argv = [sys.argv[0]] + sys.argv[3:]
        runpy.run_path("scripts/benchmark_summaries.py", run_name="__main__")
