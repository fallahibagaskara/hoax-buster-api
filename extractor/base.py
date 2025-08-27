import asyncio
import re
import time
from dataclasses import dataclass
from typing import Optional, Tuple, Dict
from urllib.parse import urlparse, urlunparse

import httpx
from bs4 import BeautifulSoup

SUPPORTED_DOMAINS = [
    "kompas.com",
    "cnnindonesia.com",
    "tempo.co",
    "detik.com",
    "liputan6.com",
    "tribunnews.com",
    "kumparan.com",
    "antaranews.com",
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
MAX_CONTENT_BYTES = 3_000_000
MIN_TEXT_CHARS = 400
CACHE_TTL_SECONDS = 300
_HOST_LIMIT = 5

_HOST_LIMITERS: Dict[str, asyncio.Semaphore] = {}
_cache: Dict[str, tuple[float, object]] = {}

@dataclass
class ExtractResult:
    text: str
    source: str
    length: int
    title: str
    content: str
    category: str | None = None
    verdict: str | None = None        
    confidence: float | None = None   
    reasons: list[str] | None = None  
    credibility_score: float | None = None 
    published_at: Optional[str] = None
    
# ---------- Util ----------
def normalize_url(raw: str) -> str:
    p = urlparse(raw)
    scheme = p.scheme or "https"
    netloc = p.netloc.lower()
    path = p.path or "/"
    return urlunparse((scheme, netloc, path, "", p.query, ""))

def public_suffix_match(host: str) -> Optional[str]:
    host = host.lower()
    for d in SUPPORTED_DOMAINS:
        if host == d or host.endswith("." + d):
            return d
    return None

def is_private_host(host: str) -> bool:
    host = host.lower()
    if (host.startswith("localhost") or host.startswith("127.")
        or host.startswith("10.") or host.startswith("192.168.")
        or host.startswith("::1")):
        return True
    if host.startswith("172."):
        try:
            oct2 = int(host.split(".")[1])
            return 16 <= oct2 <= 31
        except Exception:
            return False
    return False

def get_host_limiter(host: str) -> asyncio.Semaphore:
    if host not in _HOST_LIMITERS:
        _HOST_LIMITERS[host] = asyncio.Semaphore(_HOST_LIMIT)
    return _HOST_LIMITERS[host]

def cache_get(key: str):
    now = time.time()
    item = _cache.get(key)
    if not item:
        return None
    exp, val = item
    if exp < now:
        _cache.pop(key, None)
        return None
    return val

def cache_set(key: str, val):
    _cache[key] = (time.time() + CACHE_TTL_SECONDS, val)

def clean_text_basic(s: str) -> str:
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
    s = re.sub(r"\s+", " ", s).strip()
    return s

# ---------- HTTP fetch / AMP ----------
async def fetch_html(url: str) -> Tuple[str, str]:
    p = urlparse(url)
    host = p.netloc.lower()
    if is_private_host(host):
        raise ValueError("Menolak host private/localhost.")
    limiter = get_host_limiter(host)
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

def find_amp_href(html: str, base_url: str) -> Optional[str]:
    soup = BeautifulSoup(html, "html.parser")
    link = soup.find("link", rel=lambda v: v and "amphtml" in v.lower())
    if link and link.get("href"):
        href = link["href"]
        base = urlparse(base_url)
        amp = urlparse(href)
        if not amp.netloc:
            return urlunparse((base.scheme, base.netloc, amp.path, "", amp.query, ""))
        return href
    return None
