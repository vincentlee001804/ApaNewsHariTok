from __future__ import annotations

import textwrap
from typing import Optional

import requests


OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "llama3.1"


def summarize(text: str, max_words: int = 40) -> Optional[str]:
    """
    Use a local Ollama model to summarize the given text.

    Returns a short summary string, or None if the request fails.
    """
    if not text:
        return None

    prompt = textwrap.dedent(
        f"""
        You are a concise local news summarizer.
        Summarize the following news article in at most {max_words} words.
        Focus on the key facts (who, what, where, when).

        Article:
        \"\"\"{text.strip()}\"\"\"
        """
    ).strip()

    try:
        response = requests.post(
            OLLAMA_URL,
            json={
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
            },
            timeout=60,
        )
        response.raise_for_status()
        data = response.json()
        summary = data.get("response", "").strip()
        return summary or None
    except Exception:
        # For now, fail quietly and let the caller decide how to handle None.
        return None

