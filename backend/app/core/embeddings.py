"""Embedding seam (OpenAI-compatible API; same key/base_url as LLM)."""

from __future__ import annotations

from app.core.config import settings
from app.core.redis import json_get, json_set, model_slot, stable_key


class EmbeddingError(RuntimeError):
    """Raised when embeddings cannot be produced (missing key / API failure)."""


def embed_texts(texts: list[str], *, batch_size: int = 16) -> list[list[float]]:
    """Return one embedding vector per input string (same order)."""
    if not texts:
        return []
    if not settings.llm_api_key:
        raise EmbeddingError("未配置 LLM_API_KEY，无法向量化")

    from openai import OpenAI

    client = OpenAI(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        timeout=min(settings.llm_timeout, 60.0),
        max_retries=1,
    )
    try:
        out: list[list[float]] = []
        for i in range(0, len(texts), batch_size):
            batch = [(t or " ").strip() or " " for t in texts[i : i + batch_size]]
            # Truncate extremely long chunks to stay within provider limits
            batch = [t[:8000] for t in batch]
            with model_slot(settings.embedding_model):
                resp = client.embeddings.create(model=settings.embedding_model, input=batch)
            # API may not guarantee order; sort by index
            ordered = sorted(resp.data, key=lambda d: d.index)
            out.extend([list(d.embedding) for d in ordered])
        return out
    except EmbeddingError:
        raise
    except Exception as e:
        raise EmbeddingError(str(e)[:300]) from e
    finally:
        try:
            client.close()
        except Exception:
            pass


def embed_query(text: str) -> list[float]:
    normalized = " ".join((text or "").split())
    key = stable_key(
        "query-embedding",
        {
            "text": normalized,
            "model": settings.embedding_model,
            "base_url": settings.llm_base_url,
        },
    )
    cached = json_get(key)
    if isinstance(cached, list) and cached:
        try:
            return [float(v) for v in cached]
        except (TypeError, ValueError):
            pass

    vecs = embed_texts([normalized])
    vector = vecs[0]
    json_set(key, vector, ttl=settings.embedding_cache_ttl)
    return vector
