from __future__ import annotations

import re
import textwrap
from typing import Optional

import requests


OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "llama3.1"

ALLOWED_CATEGORIES = [
    "Emergency",
    "Health",
    "Sports",
    "Politics",
    "Education",
    "Business",
    "Technology",
    "Entertainment",
    "Environment",
    "Infrastructure",
    "Crime",
    "Social",
    "Tourism",
    "Food",
    "Local",
    "General",
]


def classify_category(text: str) -> Optional[str]:
    """
    Use the local Ollama model to classify a news item into exactly ONE category.

    Returns one of ALLOWED_CATEGORIES, or None if classification fails.
    """
    if not text or not text.strip():
        return None

    categories = ", ".join(ALLOWED_CATEGORIES)
    prompt = textwrap.dedent(
        f"""
        You are classifying a Sarawak (Malaysia) local news item into exactly ONE category.
        Choose only from this list:
        {categories}

        Rules:
        - Output EXACTLY one category word from the list above.
        - No extra words, no punctuation, no quotes.
        - If unsure, output "General".

        News text:
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
        raw = (data.get("response", "") or "").strip()

        # Normalize common mistakes
        raw = raw.strip().strip('"').strip("'")
        raw = raw.splitlines()[0].strip()

        # Exact match preferred
        if raw in ALLOWED_CATEGORIES:
            return raw

        # Case-insensitive match
        for cat in ALLOWED_CATEGORIES:
            if raw.lower() == cat.lower():
                return cat

        return None
    except Exception:
        return None


def summarize(text: str, max_words: int = 40, title: str = "") -> Optional[str]:
    """
    Use a local Ollama model to summarize the given text.

    Returns a short summary string, or None if the request fails.
    """
    if not text:
        return None

    title_line = f'Headline: "{title.strip()}"\n' if title and title.strip() else ""
    prompt = textwrap.dedent(
        f"""
        You are summarizing a local news article from Sarawak, Malaysia.
        Read the full article below and create a concise summary in at most {max_words} words.
        {title_line}
        
        Focus on:
        - Key facts: who, what, where, when, why
        - Important details and numbers mentioned
        - Main points and outcomes

        Strict relevance rules:
        - The summary MUST match the provided headline/article only.
        - Do NOT use information from other articles or prior context.
        - If the text does not contain enough matching information for the headline, output exactly: NO_SUMMARY
        
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

        if not summary:
            return None

        # Hard reject model refusal/mismatch responses.
        lowered = summary.lower()
        rejection_markers = [
            "no_summary",
            "i don't have an article",
            "i do not have an article",
            "provided text is",
            "if you'd like",
            "i can help with",
        ]
        if any(marker in lowered for marker in rejection_markers):
            return None

        # Keep output concise even if model exceeds the requested limit.
        words = re.findall(r"\S+", summary)
        if len(words) > max_words:
            summary = " ".join(words[:max_words]).strip()
        
        return summary or None
    except Exception:
        # For now, fail quietly and let the caller decide how to handle None.
        return None

