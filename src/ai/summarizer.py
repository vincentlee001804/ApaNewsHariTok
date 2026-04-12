from __future__ import annotations

import json
import re
import textwrap
from typing import Any, List, Optional

import requests

from src.core.config import (
    OLLAMA_GENERATE_URL,
    OLLAMA_MODEL,
    OLLAMA_SUMMARY_NUM_PREDICT,
    ollama_request_headers,
)
from src.core.news_categories import (
    NEWS_ARTICLE_CATEGORY_LABELS as ALLOWED_CATEGORIES,
    category_labels_for_llm_prompt,
    normalize_llm_category_token,
)


def _ollama_post(json_body: dict, timeout: int) -> requests.Response:
    return requests.post(
        OLLAMA_GENERATE_URL,
        json=json_body,
        headers=ollama_request_headers(),
        timeout=timeout,
    )

def strip_markdown_artifacts_for_plain_text(text: str) -> str:
    """
    Telegram /latest and pushes use ParseMode.HTML; summary lines are plain escaped text.
    Models often emit **bold** or *italic* (Markdown), which shows as ugly literals — strip it.
    """
    if not text:
        return text
    s = text
    for _ in range(16):
        prev = s
        s = re.sub(r"\*\*([^*]+?)\*\*", r"\1", s, flags=re.DOTALL)
        s = re.sub(r"__([^_]+?)__", r"\1", s, flags=re.DOTALL)
        s = re.sub(r"(?<!\*)\*([^*\n]+?)\*(?!\*)", r"\1", s, flags=re.DOTALL)
        if s == prev:
            break
    s = s.replace("**", "").replace("__", "")
    s = s.replace("*", "")
    return " ".join(s.split()).strip()


def clip_plain_text_to_word_limit(text: str, max_words: int) -> str:
    """
    Enforce a word cap; when truncating, try to end at the last full sentence in range.
    """
    cleaned = " ".join((text or "").split())
    if not cleaned or max_words < 1:
        return cleaned
    words = cleaned.split()
    if len(words) <= max_words:
        return cleaned
    clipped = " ".join(words[:max_words])
    for punct in (". ", "? ", "! ", "。", "？", "！"):
        pos = clipped.rfind(punct)
        if pos > int(len(clipped) * 0.45):
            return clipped[: pos + len(punct.rstrip())].strip()
    return clipped.rstrip(" ,;:.—-") + "…"


def finalize_summary_plain_text(text: str) -> str:
    """
    If the model hit a token limit or omitted final punctuation, avoid leaving a dangling clause.
    Prefer trimming back to the last full sentence; otherwise append an ellipsis.
    """
    t = " ".join((text or "").split()).strip()
    if not t:
        return t
    if t[-1] in ".!?。！？" or t.endswith("…"):
        return t
    best = -1
    for sep in (". ", "? ", "! "):
        p = t.rfind(sep)
        if p > best:
            best = p
    for sep in ("。", "？", "！"):
        p = t.rfind(sep)
        if p > best:
            best = p
    if best >= max(20, int(len(t) * 0.28)):
        return t[: best + 1].strip()
    return t + "…"


def normalize_stored_ai_summary(text: str | None, *, max_words: int = 30) -> str:
    """
    Single pipeline for text saved to news_articles.ai_summary (fixes legacy Markdown / cuts).
    """
    s = strip_markdown_artifacts_for_plain_text(text or "")
    if not s:
        return ""
    s = clip_plain_text_to_word_limit(s, max_words)
    return finalize_summary_plain_text(s)


def classify_category(text: str) -> Optional[str]:
    """
    Use the local Ollama model to classify a news item into exactly ONE category.

    Returns one of ALLOWED_CATEGORIES, or None if classification fails.
    """
    if not text or not text.strip():
        return None

    categories = category_labels_for_llm_prompt()
    prompt = textwrap.dedent(
        f"""
        You are classifying a Sarawak (Malaysia) local news item into exactly ONE category.
        Choose only from this list:
        {categories}

        Rules:
        - Output EXACTLY one category label from the list above (same spelling and capitalization as listed).
        - No extra words, no punctuation, no quotes, no explanation.
        - Pick the MAIN subject. Use "Politics" only when government, elections, policy, or legislation
          is central—not merely because an MP or minister is mentioned at a community event
          (then prefer Social, Local, Infrastructure, Culture, or Religion as appropriate).
        - Use "Infrastructure" for roads, bridges, utilities, water supply, resurfacing projects.
        - Use "Social" for community gatherings, welfare, NGOs, village requests, surau upgrades unless
          the focus is clearly Religion or Infrastructure.
        - If unsure, output "General".

        News text:
        \"\"\"{text.strip()}\"\"\"
        """
    ).strip()

    try:
        response = _ollama_post(
            {
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {"num_predict": 32},
            },
            timeout=60,
        )
        response.raise_for_status()
        data = response.json()
        raw = (data.get("response", "") or "").strip()

        normalized = normalize_llm_category_token(raw)
        return normalized
    except Exception:
        return None


def summarize(text: str, max_words: int = 30, title: str = "") -> Optional[str]:
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
        Read the full article and output a brief {max_words}-word summary only: one or two tight
        sentences with who, what, where, and the main outcome; skip minor detail if needed.
        {title_line}

        Strict relevance rules:
        - The summary MUST match the provided headline/article only.
        - Do NOT use information from other articles or prior context.
        - If the text does not contain enough matching information for the headline, output exactly: NO_SUMMARY

        Provide only the summary text, no instructions, labels, or quotes around the summary.
        Write in clear, natural language.
        End with a complete sentence (do not stop mid-thought).
        Use plain text only: no Markdown, no ** or * for bold/italic, no __underscores__.

        Full Article:
        \"\"\"{text.strip()}\"\"\"
        """
    ).strip()

    try:
        response = _ollama_post(
            {
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {"num_predict": OLLAMA_SUMMARY_NUM_PREDICT},
            },
            timeout=60,
        )
        response.raise_for_status()
        data = response.json()
        summary = data.get("response", "").strip()

        # Clean up any instruction text that might be included
        # Remove common prefixes like "Here is a summary...", "Summary:", etc.
        summary = re.sub(
            r"(?i)Here is a summary of the news article in \d+ words or less:\s*",
            "",
            summary,
        )
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

        summary = strip_markdown_artifacts_for_plain_text(summary)
        summary = clip_plain_text_to_word_limit(summary, max_words)
        summary = finalize_summary_plain_text(summary)
        return summary or None
    except Exception:
        # For now, fail quietly and let the caller decide how to handle None.
        return None


def summarize_digest(items_text: str, max_words: int = 160) -> Optional[str]:
    """
    Summarize a set of "today" news items into one digest.
    The input should already be reduced (titles + short summaries) to keep prompts small.
    """
    if not items_text or not items_text.strip():
        return None

    prompt = textwrap.dedent(
        f"""
        You are summarizing Sarawak (Malaysia) local news for today.

        You will receive multiple items. Create ONE concise digest for Telegram:
        - Output 4 to 7 bullet points.
        - Each bullet must start with "- ".
        - Focus on key themes, outcomes, and important numbers mentioned.
        - Do NOT include headings or numbering.
        - Total output must be within {max_words} words.
        - Use plain text only (no HTML, no Markdown).

        Items (titles + item summaries):
        \"\"\"{items_text.strip()}\"\"\"
        """
    ).strip()

    try:
        response = _ollama_post(
            {
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {"num_predict": max(OLLAMA_SUMMARY_NUM_PREDICT, 512)},
            },
            timeout=60,
        )
        response.raise_for_status()
        data = response.json()
        summary = (data.get("response", "") or "").strip()

        if not summary:
            return None

        lowered = summary.lower()
        rejection_markers = [
            "no_summary",
            "i don't have enough",
            "i do not have enough",
            "i can't",
            "i cannot",
            "unable to",
        ]
        if any(marker in lowered for marker in rejection_markers):
            return None

        summary = strip_markdown_artifacts_for_plain_text(summary)
        summary = clip_plain_text_to_word_limit(summary, max_words)
        summary = finalize_summary_plain_text(summary)

        return summary or None
    except Exception:
        return None


def answer_news_question(question: str, items_text: str, max_words: int = 220) -> Optional[str]:
    """
    Answer a user question using only the provided news items.
    items_text should be a compact list of "- title\\n  snippet" blocks.
    """
    if not question or not question.strip():
        return None
    if not items_text or not items_text.strip():
        return None

    prompt = textwrap.dedent(
        f"""
        You are a local news agent for Sarawak (Malaysia).
        You will be given a user question and a set of news items (titles + short snippets).

        Rules:
        - Answer the user's question ONLY using the provided items.
        - If the provided items do not contain enough relevant information, say:
          "I couldn't find relevant information in the news items I have."
        - Keep the answer concise (<= {max_words} words).
        - Use plain text only (no HTML, no Markdown).
        - Prefer bullet points when there are multiple facts.
        - Optionally end with a short "Related headlines:" line listing 2-4 titles.

        User question:
        {question.strip()}

        News items:
        \"\"\"{items_text.strip()}\"\"\"
        """
    ).strip()

    try:
        response = _ollama_post(
            {
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {"num_predict": max(OLLAMA_SUMMARY_NUM_PREDICT, 512)},
            },
            timeout=60,
        )
        response.raise_for_status()
        data = response.json()
        answer = (data.get("response", "") or "").strip()
        if not answer:
            return None

        lowered = answer.lower()
        rejection_markers = [
            "no_summary",
            "i don't have",
            "i do not have",
            "unable",
            "can't",
        ]
        if any(marker in lowered for marker in rejection_markers):
            # Still allow the specific "couldn't find relevant info..." phrasing.
            if "couldn't find relevant information" in lowered:
                return finalize_summary_plain_text(
                    strip_markdown_artifacts_for_plain_text(answer)
                )
            # Otherwise treat as failure so caller can fallback.
            return None

        answer = strip_markdown_artifacts_for_plain_text(answer)
        answer = clip_plain_text_to_word_limit(answer, max_words)
        answer = finalize_summary_plain_text(answer)
        return answer or None
    except Exception:
        return None


def fallback_waze_alert_sentence(alert: dict[str, Any]) -> str:
    """
    Deterministic one-liner when Ollama is down or batch parsing fails.
    """
    raw_type = (alert.get("type") or "report").strip()
    label = raw_type.replace("_", " ").strip().lower() or "traffic report"
    street = (alert.get("street") or "").strip() or "an unspecified road"
    city = (alert.get("city") or "").strip()
    if city:
        return f"Waze users report {label} on {street} in {city}."
    return f"Waze users report {label} on {street}."


def waze_alerts_to_news_sentences(alerts: List[dict[str, Any]]) -> List[str]:
    """
    One Ollama call: turn a list of compact Waze alert dicts into one natural sentence each.

    Falls back to :func:`fallback_waze_alert_sentence` per row if the model output
    does not line up with the input count.
    """
    if not alerts:
        return []

    compact: List[dict[str, Any]] = []
    for a in alerts:
        desc = a.get("reportDescription")
        if isinstance(desc, str) and len(desc) > 240:
            desc = desc[:237] + "..."
        compact.append(
            {
                "type": a.get("type"),
                "subtype": a.get("subtype"),
                "street": a.get("street"),
                "city": a.get("city"),
                "description": desc or None,
            }
        )

    n = len(compact)
    payload = json.dumps(compact, ensure_ascii=False)
    prompt = textwrap.dedent(
        f"""
        You translate Waze live-map alerts into short traffic news lines for drivers in Sarawak, Malaysia.

        You will receive exactly {n} alerts as a JSON array, in order.
        Output EXACTLY {n} lines of plain text.
        Line i must be ONE complete sentence describing only alert i.
        Mention the alert type, road or street name, and city when available.
        Do not number the lines, do not use bullet characters, and do not wrap sentences in quotation marks.

        Alerts (JSON):
        {payload}
        """
    ).strip()

    try:
        response = _ollama_post(
            {
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
            },
            timeout=90,
        )
        response.raise_for_status()
        data = response.json()
        raw = (data.get("response") or "").strip()
    except Exception:
        return [fallback_waze_alert_sentence(a) for a in alerts]

    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    cleaned: List[str] = []
    for ln in lines:
        ln = re.sub(r"^\d+[\).\s]+", "", ln).strip()
        if ln:
            cleaned.append(ln)

    if len(cleaned) != n:
        return [fallback_waze_alert_sentence(a) for a in alerts]

    return cleaned

