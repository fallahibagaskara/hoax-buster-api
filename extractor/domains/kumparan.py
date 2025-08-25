import re
from urllib.parse import urlparse
import trafilatura
from bs4 import BeautifulSoup
from datetime import datetime, timezone, timedelta
from ..base import ExtractResult, MIN_TEXT_CHARS, fetch_html, find_amp_href, clean_text_basic

# timezone Indonesia
_WIB = timezone(timedelta(hours=7))
_WITA = timezone(timedelta(hours=8))
_WIT = timezone(timedelta(hours=9))

_MONTH_ID = {
    "januari": 1, 
    "februari": 2, 
    "maret": 3, 
    "april": 4, 
    "mei": 5, 
    "juni": 6,
    "juli": 7, 
    "agustus": 8, 
    "september": 9, 
    "oktober": 10, 
    "november": 11, 
    "desember": 12
}

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
    node = soup.select_one('[data-qa-id="publish-date"]')
    if not node:
        return None
    txt = _norm(node.get_text(" ", strip=True))
    m = re.search(
        r'(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})\s+(\d{1,2}):(\d{2})\s*(WIB|WITA|WIT)?',
        txt, flags=re.IGNORECASE
    )
    if not m:
        return None
    dd, mon_name, yy, hh, mi, tz = m.groups()
    mon = _MONTH_ID.get(mon_name.lower())
    if not mon:
        return None
    tzinfo = {"WIB": _WIB, "WITA": _WITA, "WIT": _WIT}.get((tz or "WIB").upper(), _WIB)
    try:
        dt_local = datetime(int(yy), int(mon), int(dd), int(hh), int(mi), tzinfo=tzinfo)
        return _to_utc_iso(dt_local)
    except Exception:
        return None

def _extract_published_at(html: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    return _parse_meta_datetime(soup) or _parse_visible_datetime(soup)

def _is_noise_text(txt: str) -> bool:
    low = txt.lower()
    return (not txt) or low == "advertisement" or low.startswith("live update")

def _clean_title(raw: str) -> str:
    t = _norm(raw)
    t = re.sub(r'\s*([\-–|])\s*kumparan\b.*$', '', t, flags=re.IGNORECASE)
    t = re.sub(r'^[\'"“”‘’\[\(]+\s*', '', t)
    t = re.sub(r'\s*[\'"“”‘’\]\)]+$', '', t)
    return _norm(t)

def _extract_title_candidates(soup: BeautifulSoup) -> list[str]:
    cands = []
    h1 = soup.select_one("h1[data-qa-id='story-title']") or soup.find("h1")
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
        if ct.lower() in ("kumparan", "beranda", "news"): continue
        seen.add(k); cleaned.append(ct)
    return cleaned[0] if cleaned else None

def _preclean_html(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    selectors = [
        "figure", "figcaption", "picture", "iframe", "video", "svg",
        "[data-qa-id='desktop-article-page-below-images']",
        "[data-qa-id='breaking-news-compartment']",
        "[data-qa-id='breaking-news-compartment-list-container']",
        "[data-qa-id='end_of_article']",
        "[data-qa-id='share-wrapper']",
        "[data-qa-id='story-footer']",
        "[data-qa-id='story-author']",
        "[data-qa-id='publish-date']",
        "[data-qa-id='reading-time']",
        ".Advertisement", ".advertisement", "[aria-label='Advertisement']",
        "[id^='google_ads_iframe_']",
        "[data-qa-id='home-breadcrumb']",
        "[data-qa-id='channel-name']",
        "[data-qa-id='story-title']",
        ".Viewweb__StyledView-sc-b0snvl-0.dFRvmS"
    ]
    for sel in selectors:
        for node in soup.select(sel):
            node.decompose()
    for node in soup.find_all(True):
        txt = node.get_text(" ", strip=True)
        if txt and txt.strip().upper() == "ADVERTISEMENT":
            node.decompose()
    allowed: list[str] = []
    for sp in soup.select("span[data-qa-id='story-paragraph']"):
        if sp.find_parent(["figure", "figcaption"]) is not None:
            continue
        txt = _norm(sp.get_text(" ", strip=True))
        if not _is_noise_text(txt):
            allowed.append(txt)
    if not allowed:
        for blk in soup.select(".track_paragraph"):
            sp = blk.find("span", attrs={"data-qa-id": "story-paragraph"})
            if not sp: continue
            txt = _norm(sp.get_text(" ", strip=True))
            if not _is_noise_text(txt):
                allowed.append(txt)
    wrapper = BeautifulSoup("<article></article>", "html.parser")
    art = wrapper.article
    for p in allowed:
        tag = wrapper.new_tag("p")
        tag.string = p
        art.append(tag)
    return str(wrapper)

def _postprocess(text: str) -> str:
    t = _norm(text)
    t = re.sub(r'\bADVERTISEMENT\b', ' ', t, flags=re.IGNORECASE)
    t = re.sub(r'^\s*kumparan(?:news|bisnis|style|tech|oto|bola)\b[:\s,|-]*', ' ', t, flags=re.IGNORECASE)
    t = re.sub(r'\s*\|\s*', ' ', t)
    t = re.sub(r'\s{2,}', ' ', t).strip()
    return t

# -------- Main handler --------
async def extract(url: str) -> ExtractResult:
    html, final_url = await fetch_html(url)

    published_at = _extract_published_at(html)

    soup_title = BeautifulSoup(html, "html.parser")
    title_cands = _extract_title_candidates(soup_title)
    title = _pick_best_title(title_cands) or ""

    cleaned_html = _preclean_html(html)

    text = trafilatura.extract(
        cleaned_html,
        include_comments=False,
        include_images=False,
        favor_recall=True,
        target_language="id",
        url=final_url,
    )

    if not text or len(_norm(text)) < 200:
        soup2 = BeautifulSoup(cleaned_html, "html.parser")
        chunks = [_norm(p.get_text(" ", strip=True)) for p in soup2.find_all("p")]
        text = " ".join([c for c in chunks if c])

    if not text or len(_norm(text)) < MIN_TEXT_CHARS:
        amp = find_amp_href(html, final_url)
        if amp:
            amp_html, amp_final = await fetch_html(amp)
            published_at = _extract_published_at(amp_html) or published_at
            amp_cleaned = _preclean_html(amp_html)
            text2 = trafilatura.extract(
                amp_cleaned,
                include_comments=False,
                include_images=False,
                favor_recall=True,
                target_language="id",
                url=amp_final,
            ) or ""
            if len(_norm(text2)) > len(_norm(text or "")):
                text, final_url = text2, amp_final

    if not text or len(_norm(text)) < MIN_TEXT_CHARS:
        raise ValueError("Konten artikel berita terlalu pendek / gagal diekstrak.")

    clean = clean_text_basic(text)
    clean = _postprocess(clean)

    host = urlparse(final_url).netloc.lower()
    return ExtractResult(
        text=clean,
        source=host,
        length=len(clean),
        title=title if title else _clean_title(clean[:120]),
        content=clean,
        published_at=published_at,
    )
