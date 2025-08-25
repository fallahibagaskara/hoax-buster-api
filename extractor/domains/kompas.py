import re
from urllib.parse import urlparse, urlunparse
import trafilatura
from bs4 import BeautifulSoup
from datetime import datetime, timezone, timedelta
from ..base import ExtractResult, MIN_TEXT_CHARS, fetch_html, clean_text_basic

# timezone Indonesia
_WIB = timezone(timedelta(hours=7))
_WITA = timezone(timedelta(hours=8))
_WIT = timezone(timedelta(hours=9))

# -------- Helpers --------
def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()

def _to_utc_iso(dt_local: datetime) -> str:
    return dt_local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def _parse_meta_datetime(soup: BeautifulSoup) -> str | None:
    for m in soup.select('meta[property="article:published_time"], meta[name="pubdate"], meta[itemprop="datePublished"]'):
        val = (m.get("content") or "").strip()
        if not val:
            continue
        try:
            dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
            if not dt.tzinfo:
                dt = dt.replace(tzinfo=_WIB)
            return _to_utc_iso(dt)
        except Exception:
            mm = re.search(r'(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?', val)
            if mm:
                y, mo, d, h, mi, ss = mm.groups()
                ss = int(ss) if ss else 0
                try:
                    return _to_utc_iso(datetime(int(y), int(mo), int(d), int(h), int(mi), ss, tzinfo=_WIB))
                except Exception:
                    pass
    return None

def _parse_visible_datetime(soup: BeautifulSoup) -> str | None:
    node = soup.select_one(".read__time") or soup.find("time")
    if not node:
        return None
    txt = _norm(node.get_text(" ", strip=True))
    m = re.search(r'(\d{1,2})/(\d{1,2})/(\d{4})\s*,?\s*(\d{1,2}):(\d{2})\s*(WIB|WITA|WIT)?', txt, flags=re.IGNORECASE)
    if not m:
        return None
    dd, mo, yy, hh, mi, tz = m.groups()
    tzinfo = {"WIB": _WIB, "WITA": _WITA, "WIT": _WIT}.get((tz or "WIB").upper(), _WIB)
    try:
        dt_local = datetime(int(yy), int(mo), int(dd), int(hh), int(mi), tzinfo=tzinfo)
        return _to_utc_iso(dt_local)
    except Exception:
        return None

def _extract_published_at(html: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    return _parse_meta_datetime(soup) or _parse_visible_datetime(soup)

def _strip_prefix(t: str) -> str:
    t = re.sub(r'^\s*[A-Z][A-Z\s\.\-/()]{1,40},\s*KOMPAS\.com\s*-\s+', '', t)
    t = re.sub(r'^\s*KOMPAS\.com\s*-\s+', '', t)
    return t

def _strip_credits(t: str) -> str:
    t = re.sub(r'\(?\s*Sumber\s*:\s*Kompas\.com[^\n)]*\)?\s*', ' ', t, flags=re.IGNORECASE)
    t = re.sub(r'\b(Penulis|Reporter|Editor)\s*:\s*[^|•\n]+(?:\s*[|•]\s*[^|•\n]+)*', ' ', t, flags=re.IGNORECASE)
    return t

def _build_show_all_url(final_url: str) -> str:
    from urllib.parse import urlparse, parse_qs, urlencode
    p = urlparse(final_url)
    q = parse_qs(p.query)
    q["page"] = ["all"]
    new_query = urlencode({k: v[0] for k, v in q.items()})
    return urlunparse((p.scheme, p.netloc, p.path, "", new_query, ""))

def _collect_read_content(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.select("a.inner-link-baca-juga"):
        a.replace_with(a.get_text(" ", strip=True))
    for sel in [
        ".gate-kgplus", ".read__paging", ".kompasidRec",
        ".ads-on-body", ".ads-partner-wrap", ".advertisement", ".ads", "#ads",
        "iframe", "script", "style", ".liftdown_v2_tanda",
        ".read__byline", ".read__credit", ".read__photo", ".fb-quote",
    ]:
        for n in soup.select(sel):
            n.decompose()
    wrapper = soup.select_one(".read__content") or soup
    chunks = []
    for node in wrapper.find_all(["p", "h2", "h3"]):
        txt = _norm(node.get_text(" ", strip=True))
        if not txt: continue
        if txt.lower().startswith("baca juga"): continue
        if "terangi negeri dengan literasi" in txt.lower() and "kompas.com" in txt.lower(): continue
        chunks.append(txt)
    return _norm(" ".join(chunks))

def _clean_title(raw: str) -> str:
    t = _norm(raw)
    t = re.sub(r'\s*([\-–|])\s*Kompas\.com\b.*$', '', t, flags=re.IGNORECASE)
    t = re.sub(r'^\s*KOMPAS\.com\s*-\s+', '', t, flags=re.IGNORECASE)
    t = re.sub(r'^[\'"“”‘’\[\(]+\s*', '', t)
    t = re.sub(r'\s*[\'"“”‘’\]\)]+$', '', t)
    return _norm(t)

def _extract_title_candidates(soup: BeautifulSoup) -> list[str]:
    cands = []
    h1 = soup.select_one("h1.read__title") or soup.find("h1")
    if h1:
        cands.append(_norm(h1.get_text(" ", strip=True)))
    for m in soup.select("meta[property='og:title'], meta[name='twitter:title']"):
        content = _norm(m.get("content") or "")
        if content: cands.append(content)
    if soup.title and soup.title.string:
        cands.append(_norm(soup.title.string))
    seen, uniq = set(), []
    for t in cands:
        k = t.lower()
        if k not in seen and t:
            seen.add(k); uniq.append(t)
    return uniq

def _pick_best_title(cands: list[str]) -> str | None:
    BEST_MIN_LEN = 6
    seen = set()
    cleaned = []
    for c in cands:
        ct = _clean_title(c)
        if not ct or len(ct) < BEST_MIN_LEN: continue
        k = ct.lower()
        if k in seen: continue
        if ct.lower() in ("kompas.com", "beranda", "news", "tren"): continue
        seen.add(k); cleaned.append(ct)
    return cleaned[0] if cleaned else None

# -------- Main handler --------
async def extract(url: str) -> ExtractResult:
    html, final_url = await fetch_html(url)

    published_at = _extract_published_at(html)

    show_all_html = None
    try:
        if BeautifulSoup(html, "html.parser").select_one(".read__paging"):
            all_url = _build_show_all_url(final_url)
            show_all_html, final_url = await fetch_html(all_url)
            published_at = _extract_published_at(show_all_html) or published_at
    except Exception:
        show_all_html = None

    base_html = show_all_html or html

    soup_for_title = BeautifulSoup(base_html, "html.parser")
    title_cands = _extract_title_candidates(soup_for_title)
    title = _pick_best_title(title_cands) or ""

    manual_text = _collect_read_content(base_html)
    if manual_text and len(manual_text) >= MIN_TEXT_CHARS:
        text = manual_text
    else:
        text = trafilatura.extract(
            base_html,
            include_comments=False,
            include_images=False,
            favor_recall=True,
            target_language="id",
            url=final_url,
        ) or ""

    if not text or len(text.strip()) < MIN_TEXT_CHARS:
        raise ValueError("Konten artikel berita terlalu pendek / gagal diekstrak.")

    clean = clean_text_basic(text)
    clean = _strip_prefix(clean)
    clean = _strip_credits(clean)
    clean = re.sub(r'\bBaca juga\s*:\s*[^\n]+', ' ', clean, flags=re.IGNORECASE)
    clean = re.sub(r'\s*\|\s*', ' ', clean)
    clean = re.sub(r'\s{2,}', ' ', clean).strip()

    host = urlparse(final_url).netloc.lower()
    return ExtractResult(
        text=clean,
        source=host,
        length=len(clean),
        title=title if title else _clean_title(clean[:120]),
        content=clean,
        published_at=published_at,
    )
