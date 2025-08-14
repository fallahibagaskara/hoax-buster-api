import asyncio
import ipaddress
import re
import time
from dataclasses import dataclass
from typing import Optional, Tuple
from urllib.parse import urlparse, urlunparse

import httpx
import trafilatura
from bs4 import BeautifulSoup  # pip install beautifulsoup4

# --- Konfigurasi inti ---
SUPPORTED_DOMAINS = [
    "kompas.com",
    "cnnindonesia.com",
    "tempo.co",
    "detik.com",
    "liputan6.com",
    "tribunnews.com",
]

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "id,en;q=0.9",
}

REQUEST_TIMEOUT = 20.0
MAX_CONTENT_BYTES = 3_000_000  # 3 MB hard cap
MIN_TEXT_CHARS = 400           # di bawah ini dianggap gagal
CACHE_TTL_SECONDS = 300

# Batasan concurrency per host biar gak ngebomb situs
_HOST_LIMITERS = {}
_HOST_LIMIT = 5  # paralel per host

# Cache ringan in-memory
_cache = {}  # key -> (expires_at, value)

@dataclass
class ExtractResult:
    text: str
    source: str
    length: int
    preview: str

# ---------- Util ----------
def _normalize_url(raw: str) -> str:
    """Normalisasi skema dan strip fragment."""
    parsed = urlparse(raw)
    scheme = parsed.scheme or "https"
    netloc = parsed.netloc.lower()
    path = parsed.path or "/"
    # drop fragment & params; keep query
    return urlunparse((scheme, netloc, path, "", parsed.query, ""))

def _public_suffix_match(host: str) -> Optional[str]:
    """Cek apakah host berakhir dengan salah satu SUPPORTED_DOMAINS (suffix match)."""
    host = host.lower()
    for d in SUPPORTED_DOMAINS:
        if host == d or host.endswith("." + d):
            return d
    return None

def _is_private_ip(host: str) -> bool:
    """Resolve-less quick guard untuk host yang jelas privat/localhost."""
    # Hard checks tanpa DNS resolve (cukup untuk 95% kasus)
    priv = (
        host.startswith("localhost")
        or host.startswith("127.")
        or host.startswith("10.")
        or host.startswith("192.168.")
        or host.startswith("::1")
    )
    # Guard untuk 172.16.0.0/12
    if host.startswith("172."):
        try:
            oct2 = int(host.split(".")[1])
            if 16 <= oct2 <= 31:
                return True
        except Exception:
            pass
    return priv

def _get_host_limiter(host: str) -> asyncio.Semaphore:
    if host not in _HOST_LIMITERS:
        _HOST_LIMITERS[host] = asyncio.Semaphore(_HOST_LIMIT)
    return _HOST_LIMITERS[host]

def _cache_get(key: str):
    now = time.time()
    item = _cache.get(key)
    if not item:
        return None
    exp, val = item
    if exp < now:
        _cache.pop(key, None)
        return None
    return val

def _cache_set(key: str, val):
    _cache[key] = (time.time() + CACHE_TTL_SECONDS, val)

def _clean_text(s: str) -> str:
    # Hapus boilerplate ringan
    noise_patterns = [
        r"\bIkuti kami di[:：]?[^\n]+",
        r"\bBaca Juga[:：]?\s*",
        r"Artikel ini telah tayang[^\n]+",
        r"\bEditor[:：][^\n]+",
        r"\bPenulis[:：][^\n]+",
        r"\(.*?Baca juga.*?\)",
        r"ADVERTISEMENT\s*SCROLL TO CONTINUE WITH CONTENT",
    ]
    for pat in noise_patterns:
        s = re.sub(pat, " ", s, flags=re.IGNORECASE)
    # Normalisasi whitespace
    s = re.sub(r"\s+", " ", s).strip()
    return s

# ---------- HTTP fetch w/ retry ----------
async def _fetch_html(url: str) -> Tuple[str, str]:
    """
    Return (html, final_url) dengan retry/backoff, validasi content-type & ukuran.
    """
    parsed = urlparse(url)
    host = parsed.netloc.lower()

    if _is_private_ip(host):
        raise ValueError("Menolak host private/localhost.")

    limiter = _get_host_limiter(host)
    async with limiter:
        async with httpx.AsyncClient(follow_redirects=True, timeout=REQUEST_TIMEOUT, headers=DEFAULT_HEADERS) as client:
            backoff = 0.6
            last_exc = None
            for _ in range(3):
                try:
                    r = await client.get(url)
                    r.raise_for_status()
                    ct = r.headers.get("Content-Type", "")
                    if "text/html" not in ct and "application/xhtml+xml" not in ct:
                        raise ValueError(f"Content-Type tidak didukung: {ct}")
                    if r.num_bytes_downloaded and r.num_bytes_downloaded > MAX_CONTENT_BYTES:
                        raise ValueError("Dokumen terlalu besar.")
                    return r.text, str(r.url)
                except Exception as e:
                    last_exc = e
                    await asyncio.sleep(backoff)
                    backoff *= 2
            raise last_exc

# ---------- AMP fallback ----------
def _find_amp_href(html: str, base_url: str) -> Optional[str]:
    soup = BeautifulSoup(html, "html.parser")
    link = soup.find("link", rel=lambda v: v and "amphtml" in v.lower())
    if link and link.get("href"):
        href = link["href"]
        # absolutkan jika perlu
        parsed_base = urlparse(base_url)
        amp_url = urlparse(href)
        if not amp_url.netloc:
            # relative → absolut
            amp_abs = urlunparse((parsed_base.scheme, parsed_base.netloc, amp_url.path, "", amp_url.query, ""))
            return amp_abs
        return href
    return None

# ---------- Ekstraksi inti ----------
async def extract_article(url_raw: str) -> ExtractResult:
    url = _normalize_url(url_raw)
    parsed = urlparse(url)
    host = parsed.netloc.lower()

    domain_hit = _public_suffix_match(host)
    if not domain_hit:
        raise ValueError(f"Domain '{host}' belum didukung.")

    # Cache
    cache_key = f"extract:{url}"
    cached = _cache_get(cache_key)
    if cached:
        return cached

    html, final_url = await _fetch_html(url)

    # Try Trafilatura (mode recall + bahasa Indonesia)
    text = trafilatura.extract(
        html,
        include_comments=False,
        include_images=False,
        favor_recall=True,
        target_language="id",
        url=final_url
    )

    if not text or len(text.strip()) < MIN_TEXT_CHARS:
        # coba AMP
        amp = _find_amp_href(html, final_url)
        if amp:
            amp_html, amp_final = await _fetch_html(amp)
            text2 = trafilatura.extract(
                amp_html,
                include_comments=False,
                include_images=False,
                favor_recall=True,
                target_language="id",
                url=amp_final
            )
            if text2 and len(text2.strip()) > len(text or ""):
                text = text2
                final_url = amp_final

    if not text or len(text.strip()) < MIN_TEXT_CHARS:
        raise ValueError("Konten artikel terlalu pendek / gagal diekstrak.")

    clean = _clean_text(text)
    preview = (clean[:300] + "…") if len(clean) > 300 else clean

    result = ExtractResult(
        text=clean,
        source=host,
        length=len(clean),
        preview=preview
    )

    print(clean)
    _cache_set(cache_key, result)
    return result
