from __future__ import annotations

from app.core.config import Endpoint, EndpointConfigError, ZhipuService, settings, zhipu_endpoint
from app.core.redis import json_get, json_set, model_slot, stable_key


class EmbeddingError(RuntimeError):
    """Raised when embeddings cannot be produced (missing endpoint settings / API failure)."""


def _embedding_endpoint() -> Endpoint:
    try:
        return zhipu_endpoint(ZhipuService.EMBEDDING)
    except EndpointConfigError as e:
        raise EmbeddingError(str(e)) from e


def embed_texts(texts: list[str], *, batch_size: int = 16) -> list[list[float]]:
    """Return one embedding vector per input string (same order)."""
    if not texts:
        return []
    endpoint = _embedding_endpoint()

    from openai import OpenAI

    client = OpenAI(
        api_key=endpoint.api_key,
        base_url=endpoint.base_url,
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
            "base_url": _embedding_endpoint().base_url,
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
