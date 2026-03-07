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
        You are summarizing a local news article from Sarawak, Malaysia.
        Read the full article below and create a concise summary in at most {max_words} words.
        
        Focus on:
        - Key facts: who, what, where, when, why
        - Important details and numbers mentioned
        - Main points and outcomes
        
        Provide only the summary text, no instructions, labels, or quotes around the summary.
        Write in clear, natural language.

        Full Article:
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
        
        # Clean up any instruction text that might be included
        # Remove common prefixes like "Here is a summary...", "Summary:", etc.
        summary = summary.replace("Here is a summary of the news article in 40 words or less:", "")
        summary = summary.replace("Here is a summary:", "")
        summary = summary.replace("Summary:", "")
        summary = summary.replace("Here is the summary:", "")
        summary = summary.replace("Here's a summary:", "")
        summary = summary.strip()
        
        # Remove quotes if the entire summary is wrapped in quotes
        if summary.startswith('"') and summary.endswith('"'):
            summary = summary[1:-1].strip()
        if summary.startswith("'") and summary.endswith("'"):
            summary = summary[1:-1].strip()
        
        return summary or None
    except Exception:
        # For now, fail quietly and let the caller decide how to handle None.
        return None

