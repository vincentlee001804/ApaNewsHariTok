from __future__ import annotations

import hashlib
import logging
import math
import re
from typing import Iterable, Sequence

import requests
from sqlalchemy import bindparam, inspect, text

from src.core.config import (
    OLLAMA_EMBED_MODEL,
    OLLAMA_PRIMARY_TIMEOUT_SEC,
    RAG_HYBRID_ENABLED,
    RAG_HYBRID_RECALL_MIN_OVERLAP,
    RAG_HYBRID_W_CATEGORY,
    RAG_HYBRID_W_KEYWORD,
    RAG_HYBRID_W_LOCATION,
    RAG_VECTOR_ENABLED,
    iter_ollama_generate_targets,
    ollama_headers_for_endpoint,
)
from src.core.location_extractor import SARAWAK_LOCATION_ALIASES
from src.storage.database import SessionLocal, engine

logger = logging.getLogger(__name__)

_EMBED_CACHE: dict[str, list[float]] = {}
_TABLE_CHECK_CACHE: dict[str, bool] = {}

# Words that carry no entity/topic signal in a news question (kept local to avoid an
# import cycle with services._DEDUP_STOPWORDS).
_HYBRID_STOPWORDS: set[str] = {
    "about", "after", "again", "also", "any", "are", "been", "could", "from",
    "happened", "has", "have", "into", "just", "know", "last", "latest", "more",
    "most", "news", "only", "other", "over", "recent", "recently", "said", "says",
    "shall", "should", "some", "such", "tell", "than", "that", "the", "their",
    "them", "then", "there", "these", "this", "today", "under", "updates",
    "update", "very", "were", "what", "when", "where", "which", "while", "will",
    "with", "would", "yesterday",
}


def _distinctive_tokens(text: str | None) -> set[str]:
    raw = text or ""
    words = re.findall(r"[a-z]+", raw.lower())
    tokens = {w for w in words if len(w) >= 4 and w not in _HYBRID_STOPWORDS}
    # Acronyms carry heavy news-question signal (SEZ, API, SESCO) but are short — keep
    # them regardless of length. Match on the original casing before lowercasing.
    tokens |= {m.lower() for m in re.findall(r"\b[A-Z]{2,}\b", raw)}
    return tokens


def _query_locations(query: str) -> set[str]:
    """
    Sarawak locations named anywhere in the question. The pipeline extractor anchors
    locations at the start of a headline, but users name them mid-sentence
    ("...in Kuching?"), so scan the alias map directly (word-boundary match).
    """
    q = query.lower()
    found: set[str] = set()
    for canonical, aliases in SARAWAK_LOCATION_ALIASES.items():
        for alias in aliases:
            if re.search(rf"\b{re.escape(alias)}\b", q):
                found.add(canonical)
                break
    return found


def _article_blob(art: object) -> str:
    return (
        f"{getattr(art, 'title', '') or ''} "
        f"{(getattr(art, 'ai_summary', None) or getattr(art, 'raw_summary', None) or '')}"
    )


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


def _vector_scores(
    *,
    query: str,
    article_ids: Sequence[int],
    fetch_k: int,
) -> list[tuple[int, float]] | None:
    """
    FYP2 (pgvector): cosine scores of the top-``fetch_k`` articles (restricted to
    ``article_ids`` when given) from stored embeddings. Returns [(article_id, cosine)]
    sorted descending, or None when vector search is unavailable (disabled, not Postgres,
    table missing, Ollama embed down, or any SQL error) so the caller falls back to the
    FYP1 in-memory path.
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
                    "SELECT article_id, 1 - (embedding <=> CAST(:qv AS vector)) AS cosine "
                    "FROM article_embeddings "
                    "WHERE article_id IN :ids "
                    "ORDER BY embedding <=> CAST(:qv AS vector) "
                    "LIMIT :k"
                ).bindparams(bindparam("ids", expanding=True))
                rows = session.execute(
                    sql, {"ids": [int(i) for i in article_ids], "qv": vec_literal, "k": int(fetch_k)}
                ).all()
            else:
                rows = session.execute(
                    text(
                        "SELECT article_id, 1 - (embedding <=> CAST(:qv AS vector)) AS cosine "
                        "FROM article_embeddings "
                        "ORDER BY embedding <=> CAST(:qv AS vector) LIMIT :k"
                    ),
                    {"qv": vec_literal, "k": int(fetch_k)},
                ).all()
        scored = [(int(r[0]), float(r[1])) for r in rows]
        return scored or None
    except Exception as e:
        logger.warning("[rag] vector search failed, using in-memory fallback: %s", e)
        return None


def _vector_top_articles(
    *,
    query: str,
    article_ids: Sequence[int],
    top_k: int,
) -> list[int] | None:
    """Thin wrapper over _vector_scores returning ranked ids only (compat for callers/eval)."""
    scored = _vector_scores(query=query, article_ids=article_ids, fetch_k=max(1, top_k))
    if not scored:
        return None
    return [aid for aid, _ in scored]


def fetch_stored_vectors(article_ids: Sequence[int]) -> dict[int, list[float]] | None:
    """
    FYP2 (semantic dedup): one SQL fetch of stored embeddings for ``article_ids``.
    Returns {article_id: vector} for the ids that have rows, or None when vector storage
    is unavailable (disabled, not Postgres, table missing, or any SQL error) so callers
    skip the semantic pass and keep the Jaccard-only behavior.
    """
    ids = [int(i) for i in article_ids if i is not None]
    if not ids or not RAG_VECTOR_ENABLED:
        return None
    if not _vector_table_available():
        return None
    try:
        with SessionLocal() as session:
            rows = session.execute(
                text(
                    "SELECT article_id, embedding FROM article_embeddings "
                    "WHERE article_id IN :ids"
                ).bindparams(bindparam("ids", expanding=True)),
                {"ids": ids},
            ).all()
        out: dict[int, list[float]] = {}
        for r in rows:
            vec = _coerce_vector(r[1])
            if vec is not None:
                out[int(r[0])] = vec
        return out or None
    except Exception as e:
        logger.warning("[rag] stored-vector fetch failed, semantic dedup skipped: %s", e)
        return None


def _coerce_vector(value) -> list[float] | None:
    """pgvector columns come back as '[f,f,...]' text (or '{...}') without a type codec."""
    if value is None:
        return None
    if isinstance(value, str):
        body = value.strip().strip("[]{}")
        if not body:
            return None
        try:
            return [float(x) for x in body.split(",")]
        except ValueError:
            return None
    try:
        return [float(x) for x in value]
    except (TypeError, ValueError):
        return None


def cosine_between(a: Sequence[float] | None, b: Sequence[float] | None) -> float | None:
    """Pure-python cosine of two stored embeddings (same model => same dimension)."""
    if not a or not b or len(a) != len(b):
        return None
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return None
    return dot / (na * nb)


def _metadata_recall_ids(
    *,
    q_tokens: set[str],
    q_locs: set[str],
    candidates: Iterable,
    already: set[int],
) -> set[int]:
    """
    FYP2 hybrid recall: articles whose stored metadata matches the question directly,
    regardless of cosine. Vector-only ranking buries these (small embedding model), so
    they must be unioned into the rescored set — a boost cannot promote an article the
    fetch never returned (first hybrid eval: 3/10 -> 3/10, every miss below rank 50).
    """
    out: set[int] = set()
    if not q_tokens and not q_locs:
        return out
    for art in candidates:
        aid = getattr(art, "id", None)
        if aid is None or aid in already:
            continue
        loc = (getattr(art, "location", None) or "").lower()
        if loc and loc in q_locs:
            out.add(aid)
            continue
        if q_tokens:
            overlap = len(q_tokens & _distinctive_tokens(_article_blob(art))) / len(q_tokens)
            if overlap >= RAG_HYBRID_RECALL_MIN_OVERLAP:
                out.add(aid)
    return out


def _hybrid_rescore(
    *,
    query: str,
    scored: list[tuple[int, float]],
    articles_by_id: dict[int, object],
    q_locs: set[str] | None = None,
) -> list[int]:
    """
    FYP2 (hybrid retrieval): add metadata boosts to cosine similarity —
      + W_KEYWORD  × fraction of the question's distinctive tokens present in the article
                   (rescues rare proper nouns like "Bebuling" that embeddings underweight);
      + W_LOCATION when the question names a Sarawak location matching the article's;
      + W_CATEGORY when the question names the article's category label.
    """
    q_tokens = _distinctive_tokens(query)
    if q_locs is None:
        q_locs = _query_locations(query)

    out: list[tuple[float, int]] = []
    for aid, cosine in scored:
        art = articles_by_id.get(aid)
        if art is None:
            continue
        score = cosine
        art_tokens = _distinctive_tokens(_article_blob(art))
        if q_tokens:
            score += RAG_HYBRID_W_KEYWORD * (len(q_tokens & art_tokens) / len(q_tokens))
        art_loc = (getattr(art, "location", None) or "").lower()
        if art_loc and art_loc in q_locs:
            score += RAG_HYBRID_W_LOCATION
        cat = (getattr(art, "category", None) or "").lower()
        if cat and cat in q_tokens:
            score += RAG_HYBRID_W_CATEGORY
        out.append((score, aid))
    out.sort(key=lambda t: t[0], reverse=True)
    return [aid for _, aid in out]


def semantic_rank_articles(
    *,
    query: str,
    articles: Iterable,
    top_k: int,
) -> list:
    """
    Rank candidate articles by embedding similarity to the query.
    FYP2: pgvector cosine (fetch-deep + hybrid metadata rescoring) when available;
    otherwise falls back to the FYP1 in-memory path on any failure.
    Returns selected article objects in descending relevance.
    """
    candidates = list(articles)
    if not candidates or not query.strip():
        return []

    # FYP2 path: stored vectors (articles without embeddings simply do not surface).
    ids = [getattr(a, "id", None) for a in candidates]
    if all(i is not None for i in ids):
        top_k = max(1, top_k)
        # Fetch deeper than top_k so hybrid boosts can promote matches from below the cut.
        fetch_k = min(len(ids), max(50, top_k * 5))
        scored = _vector_scores(query=query, article_ids=ids, fetch_k=fetch_k)
        if scored:
            by_id = {getattr(a, "id"): a for a in candidates}
            ranked_ids = [aid for aid, _ in scored if aid in by_id]
            if RAG_HYBRID_ENABLED:
                q_tokens = _distinctive_tokens(query)
                q_locs = _query_locations(query)
                # Metadata recall: union in articles the question names directly that
                # cosine ranking buried; re-score the union in one vector query.
                extra = _metadata_recall_ids(
                    q_tokens=q_tokens,
                    q_locs=q_locs,
                    candidates=candidates,
                    already=set(ranked_ids),
                )
                if extra:
                    union = sorted(set(ranked_ids) | extra)
                    deep = _vector_scores(query=query, article_ids=union, fetch_k=len(union))
                    if deep:
                        scored = deep
                ranked_ids = _hybrid_rescore(
                    query=query, scored=scored, articles_by_id=by_id, q_locs=q_locs
                )
            return [by_id[i] for i in ranked_ids[:top_k] if i in by_id]

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
