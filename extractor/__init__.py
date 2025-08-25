# extractor/__init__.py
from urllib.parse import urlparse
from .base import (
    ExtractResult, SUPPORTED_DOMAINS, normalize_url, public_suffix_match,
    cache_get, cache_set
)
from .generic import extract_generic
from .domains import DOMAIN_HANDLERS

__all__ = ["extract_article", "ExtractResult", "SUPPORTED_DOMAINS"]

async def extract_article(url_raw: str) -> ExtractResult:
    url = normalize_url(url_raw)
    host = urlparse(url).netloc.lower()

    domain_hit = public_suffix_match(host)
    if not domain_hit:
        raise ValueError(f"Domain '{host}' belum didukung.")

    cache_key = f"extract:{url}"
    cached = cache_get(cache_key)
    if cached:
        return cached  # type: ignore[return-value]

    handler = DOMAIN_HANDLERS.get(domain_hit)
    if handler:
        result = await handler(url)
    else:
        result = await extract_generic(url, source_host=host)

    # Normalisasi source untuk konsistensi FE
    result.source = host
    cache_set(cache_key, result)
    return result
