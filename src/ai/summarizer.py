from __future__ import annotations

import json
import logging
import re
import textwrap
from typing import Any, List, Optional

import requests

from src.core.config import (
    OLLAMA_MODEL,
    OLLAMA_PRIMARY_TIMEOUT_SEC,
    OLLAMA_SUMMARY_NUM_PREDICT,
    iter_ollama_generate_targets,
)
from src.core.location_extractor import extract_location_and_state
from src.core.news_categories import (
    NEWS_ARTICLE_CATEGORY_LABELS as ALLOWED_CATEGORIES,
    category_labels_for_llm_prompt,
    normalize_llm_category_token,
)

logger = logging.getLogger(__name__)

_SARAWAK_LOCATION_ALIASES: dict[str, list[str]] = {
    "kota samarahan": ["kota samarahan", "samarahan"],
    "kuching": ["kuching"],
    "miri": ["miri"],
    "sibu": ["sibu"],
    "bintulu": ["bintulu"],
    "serian": ["serian"],
    "sarikei": ["sarikei"],
    "sri aman": ["sri aman", "sriaman"],
    "mukah": ["mukah"],
    "limbang": ["limbang"],
    "lawas": ["lawas"],
    "betong": ["betong"],
    "saratok": ["saratok"],
    "kapit": ["kapit"],
    "marudi": ["marudi"],
    "belaga": ["belaga"],
}

_CANONICAL_SARAWAK_LOCATION_KEYS: tuple[str, ...] = tuple(sorted(_SARAWAK_LOCATION_ALIASES.keys()))


def infer_swb_telegram_geo(title: str, body: str | None) -> tuple[str | None, str]:
    """
    Use the local LLM to infer NewsArticle.location / state for Sarawak Water Board-style Telegram posts
    (often Bahasa Melayu, multi-area or statewide). Falls back to rule-based extract_location_and_state.

    location return value:
    - comma-separated canonical place keys (e.g. "sibu,sarikei")
    - "statewide" when the post clearly affects all or most of Sarawak / multiple divisions
    - None when unknown (caller may keep heuristic column empty)
    """
    blob = f"{title or ''}\n{body or ''}".strip()
    if not blob:
        return extract_location_and_state(title or "", body)

    allowed = ", ".join(_CANONICAL_SARAWAK_LOCATION_KEYS)
    prompt = textwrap.dedent(
        f"""
        You read an official utility / water / electricity style Telegram announcement for Sarawak, Malaysia.
        It may be in Malay or English. Infer geography only from the text (do not guess places not implied).

        Return ONLY one JSON object (no markdown fences, no extra text) with exactly these keys:
        - "state": either "sarawak" or "other"
        - "coverage": one of "places", "statewide", "unknown"
        - "locations": an array of zero or more strings; each string MUST be exactly one of:
          [{allowed}]

        Rules:
        - Use lowercase for every location string.
        - If the notice affects all or most of Sarawak, many divisions, or uses phrases like
          "seluruh Sarawak" / "sebahagian besar negeri" / statewide service recovery, set coverage to "statewide"
          and locations to [].
        - If specific towns/divisions are named (e.g. Sibu, Miri, Bahagian Kuching), set coverage to "places"
          and list every distinct affected canonical location from the allowed list that is clearly implied.
        - If you cannot tell, set coverage to "unknown" and locations to [].

        Title:
        \"\"\"{(title or "").strip()}\"\"\"

        Body:
        \"\"\"{(body or "").strip()[:6000]}\"\"\"
        """
    ).strip()

    def _parse_json_obj(raw: str) -> dict[str, Any] | None:
        s = (raw or "").strip()
        if not s:
            return None
        try:
            val = json.loads(s)
            return val if isinstance(val, dict) else None
        except json.JSONDecodeError:
            pass
        start = s.find("{")
        end = s.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            val = json.loads(s[start : end + 1])
            return val if isinstance(val, dict) else None
        except json.JSONDecodeError:
            return None

    allowed_set = set(_CANONICAL_SARAWAK_LOCATION_KEYS)
    try:
        response = _ollama_post(
            {
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {"num_predict": 256},
            },
            timeout=60,
        )
        response.raise_for_status()
        data = response.json()
        raw_out = (data.get("response", "") or "").strip()
        obj = _parse_json_obj(raw_out)
        if not obj:
            raise ValueError("no json")

        state_raw = (obj.get("state") or "other").strip().lower()
        state = "sarawak" if state_raw == "sarawak" else "other"
        coverage = (obj.get("coverage") or "unknown").strip().lower()
        locs_raw = obj.get("locations")
        locs: list[str] = []
        if isinstance(locs_raw, list):
            for x in locs_raw:
                if isinstance(x, str):
                    k = x.strip().lower()
                    if k in allowed_set:
                        locs.append(k)

        if coverage == "statewide" and state == "sarawak":
            return "statewide", "sarawak"
        if locs:
            ordered = sorted(dict.fromkeys(locs))
            return ",".join(ordered), "sarawak" if state == "sarawak" else state
    except Exception:
        logger.debug("infer_swb_telegram_geo failed; using heuristic", exc_info=True)

    return extract_location_and_state(title or "", body)


def _detect_sarawak_locations(text: str) -> set[str]:
    t = f" {(text or '').lower()} "
    if not t.strip():
        return set()
    found: set[str] = set()
    for canonical, aliases in _SARAWAK_LOCATION_ALIASES.items():
        for alias in aliases:
            a = alias.strip().lower()
            if not a:
                continue
            if re.search(rf"\b{re.escape(a)}\b", t):
                found.add(canonical)
                break
    return found


def _has_conflicting_sarawak_location(*, source_text: str, generated_text: str) -> bool:
    """
    Return True when generated output mentions a Sarawak place that conflicts
    with the place(s) explicitly present in source text.
    """
    source_locs = _detect_sarawak_locations(source_text)
    generated_locs = _detect_sarawak_locations(generated_text)
    if not source_locs or not generated_locs:
        return False
    return generated_locs.isdisjoint(source_locs)


def _ollama_post(json_body: dict, timeout: int) -> requests.Response:
    """
    Try primary Ollama (usually local), then OLLAMA_API_BASE_FALLBACK if configured.
    When two targets exist, the first uses a shorter timeout so a sleeping host fails fast.
    """
    targets = iter_ollama_generate_targets()
    last_exc: Exception | None = None
    multi = len(targets) > 1
    for i, (url, headers, model, use_short_timeout) in enumerate(targets):
        payload = {**json_body, "model": model}
        t = timeout
        if multi and use_short_timeout:
            t = min(timeout, OLLAMA_PRIMARY_TIMEOUT_SEC)
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=t)
            response.raise_for_status()
            return response
        except Exception as exc:
            last_exc = exc
            host = url.rsplit("/api/", 1)[0]
            logger.debug("Ollama request failed (%s): %s", host, exc)
            continue
    assert last_exc is not None
    raise last_exc

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


def normalize_stored_ai_summary(text: str | None, *, max_words: int | None = None) -> str:
    """
    Pipeline for news_articles.ai_summary and Telegram display: strip Markdown artifacts,
    optional word cap, light finish. Default is no word cap so the full model output is kept.
    """
    s = strip_markdown_artifacts_for_plain_text(text or "")
    if not s:
        return ""
    if max_words is not None and max_words > 0:
        s = clip_plain_text_to_word_limit(s, max_words)
    return finalize_summary_plain_text(s)


def normalize_stored_ai_title(text: str | None, *, max_words: int = 14) -> str:
    """
    Normalize an AI-generated display title: plain text, one line, concise.
    """
    s = strip_markdown_artifacts_for_plain_text(text or "")
    if not s:
        return ""
    s = " ".join(s.split()).strip()
    s = clip_plain_text_to_word_limit(s, max_words)
    s = s.rstrip(" ,;:")
    if len(s) > 220:
        s = s[:220].rstrip(" ,.;:!?") + "..."
    return s


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
    Use Ollama to summarize the given text. The prompt asks for about ``max_words`` words, but the
    returned text is not hard-clipped so longer accurate summaries are preserved end-to-end.
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
        - If a place/location is mentioned in the article or headline, keep that exact place in the summary.
          Do not replace it with another city.

        Provide only the summary text, no instructions, labels, or quotes around the summary.
        Write in clear, natural English using simple everyday words.
        If the source is in Malay or another language, translate faithfully into English.
        Avoid jargon, legal wording, and technical terms unless necessary.
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
        summary = finalize_summary_plain_text(summary)
        source_blob = f"{title or ''}\n{text or ''}"
        if _has_conflicting_sarawak_location(
            source_text=source_blob,
            generated_text=summary,
        ):
            return None
        return summary or None
    except Exception:
        # For now, fail quietly and let the caller decide how to handle None.
        return None


def generate_display_title(
    *,
    text: str,
    title_hint: str = "",
    max_words: int = 14,
) -> Optional[str]:
    """
    Generate a concise, reader-friendly display title in English.
    Useful when sources mix Malay/English headlines.
    """
    clean_text = " ".join((text or "").split()).strip()
    clean_title_hint = " ".join((title_hint or "").split()).strip()
    if not clean_text and not clean_title_hint:
        return None

    prompt = textwrap.dedent(
        f"""
        You write ONE concise English display title for Sarawak local news.
        Use the article summary/context as the primary source of truth.
        Use headline hint only as secondary support if useful.

        Rules:
        - Output exactly one title line only (no bullets, no numbering, no labels).
        - Keep meaning faithful to the provided summary/context.
        - Use simple clear English.
        - Keep under {max_words} words.
        - No quotes around the title.
        - No Markdown/HTML.
        - Do not invent facts not present in the provided text.
        - If a place/location appears in the provided text/hint, preserve that place.
          Do not swap it to another city.

        Summary/context (primary):
        \"\"\"{clean_text}\"\"\"

        Headline hint (optional):
        \"\"\"{clean_title_hint}\"\"\"
        """
    ).strip()

    try:
        response = _ollama_post(
            {
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {"num_predict": 96},
            },
            timeout=45,
        )
        response.raise_for_status()
        data = response.json()
        generated = (data.get("response", "") or "").strip()
        if not generated:
            return None

        generated = generated.splitlines()[0].strip()
        generated = re.sub(r"(?i)^(title|headline)\s*[:\-]\s*", "", generated).strip()
        if generated.startswith('"') and generated.endswith('"'):
            generated = generated[1:-1].strip()
        if generated.startswith("'") and generated.endswith("'"):
            generated = generated[1:-1].strip()

        normalized = normalize_stored_ai_title(generated, max_words=max_words)
        if not normalized:
            return None
        source_blob = f"{clean_title_hint}\n{clean_text}"
        if _has_conflicting_sarawak_location(
            source_text=source_blob,
            generated_text=normalized,
        ):
            return None
        return normalized
    except Exception:
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
        - Use simple, easy-to-understand English for general readers (translate if needed).

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


def summarize_digest_overview(
    items_text: str, *, story_count: int, max_words: int = 60
) -> Optional[str]:
    """
    Create one short overview paragraph for a digest header.
    """
    if not items_text or not items_text.strip():
        return None

    prompt = textwrap.dedent(
        f"""
        You are preparing a Sarawak local-news digest intro.

        Write ONE short paragraph (no bullets) for Telegram that:
        - mentions there are about {story_count} relevant stories,
        - highlights key themes and likely places involved (if clear from input),
        - stays within {max_words} words,
        - uses plain text only (no HTML/Markdown),
        - uses simple, reader-friendly English (translate if needed).

        Strict output rules:
        - Output ONLY the paragraph content.
        - Do NOT add labels like "Today's highlights", "Here is...", or "Intro paragraph".
        - Do NOT wrap the paragraph in quotes.
        - Do NOT use placeholders like [location], [theme], etc.
        - Avoid generic openings like "Sarawak takes centre stage" or "in today's local news".
        - Start with concrete developments from the provided items.

        Items (titles + snippets):
        \"\"\"{items_text.strip()}\"\"\"
        """
    ).strip()

    try:
        response = _ollama_post(
            {
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {"num_predict": max(OLLAMA_SUMMARY_NUM_PREDICT, 256)},
            },
            timeout=45,
        )
        response.raise_for_status()
        data = response.json()
        text = (data.get("response", "") or "").strip()
        if not text:
            return None
        text = strip_markdown_artifacts_for_plain_text(text)
        # Remove common meta wrappers if the model still adds them.
        text = re.sub(
            r'(?i)^(today[\'’]?s highlights|highlights|intro(?:ductory)? paragraph|here is (?:the )?(?:intro|summary|paragraph))\s*[:\-]\s*',
            "",
            text,
        ).strip()
        if text.startswith('"') and text.endswith('"'):
            text = text[1:-1].strip()
        if text.startswith("'") and text.endswith("'"):
            text = text[1:-1].strip()
        text = clip_plain_text_to_word_limit(text, max_words)
        text = finalize_summary_plain_text(text)
        return text or None
    except Exception:
        return None


def generate_digest_greeting(period: str) -> Optional[str]:
    """
    Generate a short one-line greeting for digest pushes.
    """
    p = (period or "").strip().lower()
    if p not in {"morning", "evening"}:
        p = "day"

    prompt = textwrap.dedent(
        f"""
        You are a friendly Sarawak local news assistant.
        Write exactly ONE short greeting line for a Telegram digest push.
        Context: It is {p}.

        Rules:
        - ENGLISH ONLY.
        - Keep it short (about 10 to 24 words), but complete.
        - Warm, supportive, and lightly emotional tone.
        - Start with "Good morning" or "Good evening" according to the context.
        - Mention this is the user's local news digest.
        - You may add a gentle closing like "rest well" or "have a peaceful night".
        - Plain text only (no Markdown, no HTML).
        - No quotes and no bullet points.
        - You may include at most ONE friendly emoji (optional).
        - End as one complete sentence.
        - Do NOT add labels like "Greeting:" or "Message:".
        """
    ).strip()

    try:
        response = _ollama_post(
            {
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {"num_predict": 64},
            },
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        text = (data.get("response", "") or "").strip()
        if not text:
            return None
        text = strip_markdown_artifacts_for_plain_text(text)
        # Remove wrappers and labels if the model still emits them.
        text = re.sub(r"(?i)^(greeting|message)\s*[:\-]\s*", "", text).strip()
        if text.startswith('"') and text.endswith('"'):
            text = text[1:-1].strip()
        if text.startswith("'") and text.endswith("'"):
            text = text[1:-1].strip()
        # Keep only the first line in case the model spills extra lines.
        text = text.splitlines()[0].strip()
        text = finalize_summary_plain_text(text)
        lowered = text.lower()
        malay_markers = [
            "selamat",
            "berita tempatan",
            "berita",
            "hari ini",
            "untukmu",
            "nikmati",
            "rehat",
            "mimpi",
        ]
        if any(marker in lowered for marker in malay_markers):
            return None
        if p == "morning" and not lowered.startswith("good morning"):
            return None
        if p == "evening" and not lowered.startswith("good evening"):
            return None
        if len(text.split()) > 26:
            return None
        # Avoid overly short, awkward output.
        if len(text.split()) < 6:
            return None
        return text or None
    except Exception:
        return None


def answer_news_question(question: str, items_text: str, max_words: int = 90) -> Optional[str]:
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
        You are "Apa News Hari Tok?", an AI local news assistant for Sarawak (Malaysia).
        Role and boundaries:
        - You summarize and answer questions using ONLY the provided local news items.
        - You do not browse the web or invent facts outside those items.
        - If no relevant article exists in the provided items, you must say so clearly.

        You will be given a user question and a set of news items (titles + short snippets).

        Rules:
        - Answer the user's question ONLY using the provided items.
        - If the provided items do not contain enough relevant information, say:
          "I couldn't find relevant information in the news items I have."
        - Keep the answer short (<= {max_words} words).
        - Use plain text only (no HTML, no Markdown).
        - Use simple, easy-to-understand English (translate if needed).
        - Use 1 short paragraph. Avoid long lists.

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

