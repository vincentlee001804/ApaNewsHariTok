from __future__ import annotations

import hashlib
import logging
import math
from typing import Iterable, Sequence

import requests
from sqlalchemy import bindparam, inspect, text

from src.core.config import (
    OLLAMA_EMBED_MODEL,
    OLLAMA_PRIMARY_TIMEOUT_SEC,
    RAG_VECTOR_ENABLED,
    iter_ollama_generate_targets,
    ollama_headers_for_endpoint,
)
from src.storage.database import SessionLocal, engine

logger = logging.getLogger(__name__)

_EMBED_CACHE: dict[str, list[float]] = {}
_TABLE_CHECK_CACHE: dict[str, bool] = {}


def _vector_table_available() -> bool:
    """Cached check — reflection queries cost seconds; table state only changes at deploy."""
    cached = _TABLE_CHECK_CACHE.get("ok")
    if cached is not None:
        return cached
    try:
        ok = (
            engine.dialect.name == "postgresql"
            and inspect(engine).has_table("article_embeddings")
        )
    except Exception:
        ok = False
    _TABLE_CHECK_CACHE["ok"] = ok
    return ok


def _embedding_url_from_generate_url(generate_url: str) -> str:
    base = generate_url.rsplit("/api/generate", 1)[0]
    return f"{base}/api/embeddings"


def _embed_text(text: str, timeout: int = 30, *, kind: str = "document") -> list[float] | None:
    """
    Embed text with Ollama. ``kind`` selects the nomic-embed-text contrastive prefix
    ('search_query: ' / 'search_document: ') — the model was trained with these prefixes,
    and omitting them measurably degrades retrieval (see docs/fyp2/02-pgvector-rag.md).
    """
    if not text or not text.strip():
        return None
    prefix = "search_query: " if kind == "query" else "search_document: "
    normalized = prefix + text.strip()
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
                json={"model": OLLAMA_EMBED_MODEL or model, "prompt": normalized},
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


def _vector_top_articles(
    *,
    query: str,
    article_ids: Sequence[int],
    top_k: int,
) -> list[int] | None:
    """
    FYP2 (pgvector): rank article_ids by cosine similarity to the query using the stored
    article_embeddings. Returns a ranked list of article_ids, or None when vector search is
    unavailable (disabled, not Postgres, table missing, Ollama embed down, or any SQL error)
    so the caller falls back to the FYP1 in-memory path.
    """
    if not RAG_VECTOR_ENABLED:
        return None
    if not _vector_table_available():
        return None

    query_vec = _embed_text(query, kind="query")
    if not query_vec:
        return None
    vec_literal = "[" + ",".join(f"{x:.6f}" for x in query_vec) + "]"

    try:
        with SessionLocal() as session:
            if article_ids:
                sql = text(
                    "SELECT article_id FROM article_embeddings "
                    "WHERE article_id IN :ids "
                    "ORDER BY embedding <=> CAST(:qv AS vector) "
                    "LIMIT :k"
                ).bindparams(bindparam("ids", expanding=True))
                rows = session.execute(
                    sql, {"ids": [int(i) for i in article_ids], "qv": vec_literal, "k": int(top_k)}
                ).all()
            else:
                rows = session.execute(
                    text(
                        "SELECT article_id FROM article_embeddings "
                        "ORDER BY embedding <=> CAST(:qv AS vector) LIMIT :k"
                    ),
                    {"qv": vec_literal, "k": int(top_k)},
                ).all()
        ranked = [int(r[0]) for r in rows]
        return ranked or None
    except Exception as e:
        logger.warning("[rag] vector search failed, using in-memory fallback: %s", e)
        return None


def semantic_rank_articles(
    *,
    query: str,
    articles: Iterable,
    top_k: int,
) -> list:
    """
    Rank candidate articles by embedding similarity to the query.
    FYP2: uses stored pgvector embeddings when available; otherwise (or on any failure)
    falls back to the FYP1 in-memory cosine path.
    Returns selected article objects in descending relevance.
    """
    candidates = list(articles)
    if not candidates or not query.strip():
        return []

    # FYP2 path: stored vectors (articles without embeddings simply do not surface).
    ids = [getattr(a, "id", None) for a in candidates]
    if all(i is not None for i in ids):
        ranked_ids = _vector_top_articles(query=query, article_ids=ids, top_k=max(1, top_k))
        if ranked_ids:
            by_id = {getattr(a, "id"): a for a in candidates}
            return [by_id[i] for i in ranked_ids if i in by_id]

    # FYP1 fallback: embed the query and every candidate on the fly, cosine in memory.
    query_vec = _embed_text(query, kind="query")
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
