import re
from urllib.parse import urlparse
import trafilatura
from bs4 import BeautifulSoup, Tag
from datetime import datetime, timezone, timedelta
from ..base import ExtractResult, MIN_TEXT_CHARS, fetch_html, find_amp_href, clean_text_basic

# timezone Indonesia
_WIB  = timezone(timedelta(hours=7))
_WITA = timezone(timedelta(hours=8))
_WIT  = timezone(timedelta(hours=9))

_MONTH_ID = {
    "jan":"01","januari":"01",
    "feb":"02","februari":"02",
    "mar":"03","maret":"03",
    "apr":"04","april":"04",
    "mei":"05",
    "jun":"06","juni":"06",
    "jul":"07","juli":"07",
    "agu":"08","agustus":"08",
    "sep":"09","september":"09",
    "okt":"10","oktober":"10",
    "nov":"11","november":"11",
    "des":"12","desember":"12",
}

def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()

def _to_utc_iso(dt_local: datetime) -> str:
    return dt_local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def _clean_title(raw: str) -> str:
    t = _norm(raw)
    t = re.sub(r'\s*([\-–|])\s*(?:ANTARA(?:\s*News)?|antaranews\.com)\b.*$', '', t, flags=re.IGNORECASE)
    t = re.sub(r'^[\'"“”‘’\[\(]+\s*', '', t)
    t = re.sub(r'\s*[\'"“”‘’\]\)]+$', '', t)
    return _norm(t)

def _extract_title_candidates(soup: BeautifulSoup) -> list[str]:
    cands = []

    h1 = soup.select_one(".wrap__article-detail-title h1") or soup.find("h1")
    if h1:
        cands.append(_norm(h1.get_text(" ", strip=True)))

    for m in soup.select("meta[property='og:title'], meta[name='twitter:title']"):
        content = _norm(m.get("content") or "")
        if content:
            cands.append(content)

    if soup.title and soup.title.string:
        cands.append(_norm(soup.title.string))

    seen, uniq = set(), []
    for t in cands:
        k = t.lower()
        if t and k not in seen:
            seen.add(k); uniq.append(t)
    return uniq

def _pick_best_title(cands: list[str]) -> str | None:
    BEST_MIN_LEN = 6
    seen = set()
    cleaned = []
    for c in cands:
        ct = _clean_title(c)
        if not ct or len(ct) < BEST_MIN_LEN:
            continue
        k = ct.lower()
        if k in seen:
            continue
        if ct.lower() in ("antara", "antara news", "antaranews.com", "beranda", "news"):
            continue
        seen.add(k)
        cleaned.append(ct)
    return cleaned[0] if cleaned else None

def _parse_meta_datetime(soup: BeautifulSoup) -> str | None:
    for m in soup.select('meta[property="article:published_time"], meta[itemprop="datePublished"], meta[name="pubdate"]'):
        val = (m.get("content") or "").strip()
        if not val:
            continue
        try:
            dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
            if not dt.tzinfo:
                dt = dt.replace(tzinfo=_WIB)
            return _to_utc_iso(dt)
        except Exception:
            m2 = re.search(r'(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?', val)
            if m2:
                y, mo, d, h, mi, ss = m2.groups()
                ss = int(ss) if ss else 0
                try:
                    dt = datetime(int(y), int(mo), int(d), int(h), int(mi), ss, tzinfo=_WIB)
                    return _to_utc_iso(dt)
                except Exception:
                    pass
    return None

def _parse_visible_datetime(soup: BeautifulSoup) -> str | None:
    for node in soup.select(".wrap__article-detail-info li span, .wrap__article-detail-info, time, .date"):
        txt = _norm(node.get_text(" ", strip=True))
        if not txt:
            continue

        m = re.search(
            r'(senin|selasa|rabu|kamis|jumat|sabtu|minggu)\s*,?\s*'
            r'(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})\s+'               
            r'(\d{1,2}):(\d{2})\s*(WIB|WITA|WIT)?',               
            txt, flags=re.IGNORECASE
        )
        if not m:
            continue
        dd = int(m.group(2))
        mon_raw = m.group(3).lower()
        yyyy = int(m.group(4))
        hh = int(m.group(5))
        mm = int(m.group(6))
        tz_raw = (m.group(7) or "WIB").upper()
        mon = _MONTH_ID.get(mon_raw)
        if not mon:
            continue
        tzinfo = {"WIB": _WIB, "WITA": _WITA, "WIT": _WIT}.get(tz_raw, _WIB)
        try:
            dt_local = datetime(year=yyyy, month=int(mon), day=dd, hour=hh, minute=mm, tzinfo=tzinfo)
            return _to_utc_iso(dt_local)
        except Exception:
            continue
    return None

def _extract_published_at(html: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    return _parse_meta_datetime(soup) or _parse_visible_datetime(soup)

def _preclean_html(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")

    container = soup.select_one(".wrap__article-detail-content") or soup
    for sel in [
        "figure", "figcaption", "picture", "iframe", "script", "style",
        ".adsbygoogle", ".advertisement", "[data-ad]", ".adv", ".ad",
        ".baca-juga", ".baca_juga", ".related", ".tag",
        ".alert", ".alert-info", ".copyright", ".wrap__article-detail-image",
        ".wrap__article-detail-info .list-inline-item .fa-clock",  # waktu baca
    ]:
        for n in container.select(sel):
            n.decompose()

    for n in container.find_all(True):
        txt = _norm(n.get_text(" ", strip=True))
        if not txt:
            continue
        low = txt.lower()
        if "dilarang keras mengambil konten" in low and "antaranews" in low:
            n.decompose()

    allowed: list[str] = []
    for node in container.find_all(["p", "h2", "h3", "blockquote"], recursive=True):
        if node.name in ("p", "blockquote"):
            txt = _norm(node.get_text(" ", strip=True))
            if not txt or txt.lower().startswith("baca juga"):
                continue
            if re.match(r'^(Pewarta|Editor)\s*:', txt, flags=re.IGNORECASE):
                continue
            if "copyright © antara" in txt.lower():
                continue
            allowed.append(txt)
        else:
            t = _norm(node.get_text(" ", strip=True))
            if t:
                allowed.append(t)

    wrapper = BeautifulSoup("<article></article>", "html.parser")
    art = wrapper.article
    for t in allowed:
        p = wrapper.new_tag("p")
        p.string = t
        art.append(p)
    return str(wrapper)

def _postprocess(text: str) -> str:
    t = _norm(text)
    t = re.sub(r'\bBaca juga\s*:\s*[^\n]+', ' ', t, flags=re.IGNORECASE)
    t = re.sub(r'\b(Pewarta|Editor)\s*:\s*[^\n]+', ' ', t, flags=re.IGNORECASE)
    t = re.sub(r'Copyright\s*©\s*ANTARA\s*\d{4}', ' ', t, flags=re.IGNORECASE)
    t = re.sub(r'^\s*.+?\(ANTARA\)\s*[—–-]\s*', ' ', t, flags=re.IGNORECASE)
    t = re.sub(r'\s*\|\s*', ' ', t)
    t = re.sub(r'\s{2,}', ' ', t).strip()
    return t

# ---------- Main handler ----------
async def extract(url: str) -> ExtractResult:
    html, final_url = await fetch_html(url)

    published_at = _extract_published_at(html)

    cleaned_html = _preclean_html(html)
    text = trafilatura.extract(
        cleaned_html,
        include_comments=False,
        include_images=False,
        favor_recall=True,
        target_language="id",
        url=final_url,
    )

    if not text or len(text.strip()) < MIN_TEXT_CHARS:
        amp = find_amp_href(html, final_url)
        if amp:
            amp_html, amp_final = await fetch_html(amp)
            text2 = trafilatura.extract(
                _preclean_html(amp_html),
                include_comments=False,
                include_images=False,
                favor_recall=True,
                target_language="id",
                url=amp_final,
            )
            if text2 and len(text2.strip()) > len(text or ""):
                text, final_url = text2, amp_final
                published_at = _extract_published_at(amp_html) or published_at

    if not text or len(text.strip()) < MIN_TEXT_CHARS:
        raise ValueError("Konten artikel berita terlalu pendek / gagal diekstrak.")

    clean = clean_text_basic(text)
    clean = _postprocess(clean)

    host = urlparse(final_url).netloc.lower()
    title = _pick_best_title(_extract_title_candidates(BeautifulSoup(html, "html.parser"))) or _clean_title(clean[:120])

    return ExtractResult(
        text=clean,
        source=host,
        length=len(clean),
        title=title,
        content=clean,
        published_at=published_at,
    )
