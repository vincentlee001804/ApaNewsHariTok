from __future__ import annotations

import hashlib
import math
from typing import Iterable, Sequence

import requests

from src.core.config import (
    OLLAMA_EMBED_MODEL,
    OLLAMA_PRIMARY_TIMEOUT_SEC,
    iter_ollama_generate_targets,
    ollama_headers_for_endpoint,
)

_EMBED_CACHE: dict[str, list[float]] = {}


def _embedding_url_from_generate_url(generate_url: str) -> str:
    base = generate_url.rsplit("/api/generate", 1)[0]
    return f"{base}/api/embeddings"


def _embed_text(text: str, timeout: int = 30) -> list[float] | None:
    if not text or not text.strip():
        return None
    normalized = text.strip()
    key = hashlib.sha1(normalized.encode("utf-8")).hexdigest()
    cached = _EMBED_CACHE.get(key)
    if cached:
        return cached

    targets = iter_ollama_generate_targets()
    multi = len(targets) > 1
    for _url, _headers, model, use_short_timeout in targets:
        embed_url = _embedding_url_from_generate_url(_url)
        headers = ollama_headers_for_endpoint(embed_url, is_fallback=not use_short_timeout)
        req_timeout = min(timeout, OLLAMA_PRIMARY_TIMEOUT_SEC) if multi and use_short_timeout else timeout
        try:
            response = requests.post(
                embed_url,
                json={"model": OLLAMA_EMBED_MODEL or model, "prompt": text.strip()},
                headers=headers,
                timeout=req_timeout,
            )
            response.raise_for_status()
            data = response.json()
            vec = data.get("embedding")
            if isinstance(vec, list) and vec:
                out = [float(x) for x in vec]
                _EMBED_CACHE[key] = out
                return out
        except Exception:
            continue
    return None


def _cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b or len(a) != len(b):
        return -1.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return -1.0
    return dot / (na * nb)


def _article_text_for_embedding(article) -> str:
    title = (getattr(article, "title", "") or "").strip()
    summary = (
        (getattr(article, "ai_summary", None) or getattr(article, "raw_summary", None) or "").strip()
    )
    category = (getattr(article, "category", "") or "").strip()
    location = (getattr(article, "location", "") or "").strip()
    state = (getattr(article, "state", "") or "").strip()
    parts = [p for p in [title, summary, category, location, state] if p]
    return "\n".join(parts)


def semantic_rank_articles(
    *,
    query: str,
    articles: Iterable,
    top_k: int,
) -> list:
    """
    Rank candidate articles by embedding similarity to the query.
    Returns selected article objects in descending relevance.
    """
    candidates = list(articles)
    if not candidates or not query.strip():
        return []

    query_vec = _embed_text(query)
    if not query_vec:
        return []

    scored: list[tuple[float, object]] = []
    for art in candidates:
        text = _article_text_for_embedding(art)
        if not text:
            continue
        vec = _embed_text(text)
        if not vec:
            continue
        score = _cosine_similarity(query_vec, vec)
        if score <= -1.0:
            continue
        scored.append((score, art))

    scored.sort(key=lambda t: t[0], reverse=True)
    return [art for _, art in scored[: max(1, top_k)]]
