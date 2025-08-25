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

# -------- Helpers --------
def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()

def _clean_title(raw: str) -> str:
    t = _norm(raw)
    t = re.sub(r'\s*([\-–|])\s*detik(?:com|\w+)\b.*$', '', t, flags=re.IGNORECASE)
    t = re.sub(r'^[\'"“”‘’\[\(]+\s*', '', t)
    t = re.sub(r'\s*[\'"“”‘’\]\)]+$', '', t)
    return _norm(t)

def _pick_best_title(cands: list[str]) -> str | None:
    seen = set()
    BEST_MIN_LEN = 6
    cleaned = []
    for c in cands:
        ct = _clean_title(c)
        if not ct or len(ct) < BEST_MIN_LEN:
            continue
        key = ct.lower()
        if key in seen: 
            continue
        if ct.lower() in ("detikcom", "detik", "home", "news"):
            continue
        seen.add(key)
        cleaned.append(ct)
    return cleaned[0] if cleaned else None

def _extract_title_candidates(soup: BeautifulSoup) -> list[str]:
    cands = []
    h1 = soup.find("h1")
    if h1:
        cands.append(_norm(h1.get_text(" ", strip=True)))
    for sel in [
        ".detail__title", ".article__title", ".title", ".headline",
        "meta[property='og:title']", "meta[name='twitter:title']"
    ]:
        if sel.startswith("meta"):
            for m in soup.select(sel):
                content = _norm(m.get("content") or "")
                if content: cands.append(content)
        else:
            for n in soup.select(sel):
                t = _norm(n.get_text(" ", strip=True))
                if t: cands.append(t)
    uniq, seen = [], set()
    for t in cands:
        k = t.lower()
        if k not in seen:
            seen.add(k); uniq.append(t)
    return uniq

def _strip_leading_title(text: str, title_candidates: list[str]) -> str:
    t = text
    for raw in title_candidates:
        title = _norm(raw)
        if not title or len(title) < 5:
            continue
        title_re = re.escape(title)
        pattern = r'\A\s*' + title_re + r'(?:\s*(?:[-–|]\s*detik(?:com|\w+)\b.*)?)?\s*[:\-–,]?\s*'
        new_t, n = re.subn(pattern, '', t, flags=re.IGNORECASE)
        if n > 0:
            t = new_t
            break
    return _norm(t)

def _to_utc_iso(dt_local: datetime) -> str:
    return dt_local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

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
    for node in soup.select(".detail__date, .date, time"):
        txt = _norm(node.get_text(" ", strip=True))
        if not txt:
            continue
        m = re.search(
            r'(senin|selasa|rabu|kamis|jumat|sabtu|minggu)\s*,?\s*'
            r'(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})\s+(\d{1,2}):(\d{2})\s*(WIB|WITA|WIT)?',
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
        tzmap = {"WIB": _WIB,
                 "WITA": _WITA,
                 "WIT": _WIT}
        tzinfo = tzmap.get(tz_raw, _WIB)
        try:
            dt_local = datetime(year=yyyy, month=int(mon), day=dd, hour=hh, minute=mm, tzinfo=tzinfo)
            return _to_utc_iso(dt_local)
        except Exception:
            continue
    return None

def _extract_published_at(html: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    return _parse_meta_datetime(soup) or _parse_visible_datetime(soup)

def _preclean_html(html: str) -> tuple[str, list[str]]:
    soup = BeautifulSoup(html, "html.parser")
    title_candidates = _extract_title_candidates(soup)

    selectors = [
        "figure", "figcaption",
        ".detail__media", ".media__caption", ".photo__caption", ".foto__caption",
        ".img__caption", ".image__caption", ".pic__caption", ".caption", ".caption__text",
        ".artikel__foto", ".media__credit", ".read__also", ".read__also--item",
        ".read__more", ".advertisement", ".ad__slot", ".parallax__caption",
    ]
    for sel in selectors:
        for node in soup.select(sel):
            node.decompose()

    for node in soup.find_all(True):
        txt = node.get_text(" ", strip=True)
        if not txt: 
            continue
        low = txt.lower()
        if (low.startswith("foto:") or " foto:" in low or " detik" in low) and len(txt) <= 220:
            node.decompose()

    for node in soup.find_all(True):
        txt = node.get_text(" ", strip=True)
        if not txt:
            continue
        low = txt.lower()
        if ("saksikan" in low or "tonton" in low) and "detik" in low:
            node.decompose()

    return str(soup), title_candidates

def _postprocess(text: str, title_candidates: list[str]) -> str:
    t = text
    t = re.sub(r'\s*\b[Ff]oto[:：]\s*[^。.!?\n\r|]*?(detik(?:com)?|detik\w+)[^。.!?\n\r|]*\s*\|?', ' ', t)
    t = re.sub(r'\s*\b[Ss]aksikan\b[^:]{0,100}\bdetik\w*[^:]{0,100}:', ' ', t)
    t = re.sub(r'\s*\b[Tt]onton\b[^:]{0,100}\bdetik\w*[^:0-9]{0,100}:', ' ', t)
    t = re.sub(r'\s*\([a-z]{2,5}/[a-z]{2,5}\)\s*', ' ', t, flags=re.IGNORECASE)
    t = _strip_leading_title(_norm(t), title_candidates)
    t = re.sub(r'\s*\|\s*', ' ', t)
    t = re.sub(r'\s{2,}', ' ', t).strip()
    return t

# -------- Main handler --------
async def extract(url: str) -> ExtractResult:
    html, final_url = await fetch_html(url)

    published_at = _extract_published_at(html)

    cleaned_html, title_cands = _preclean_html(html)
    text = trafilatura.extract(
        cleaned_html, include_comments=False, include_images=False,
        favor_recall=True, target_language="id", url=final_url
    )

    if not text or len(text.strip()) < MIN_TEXT_CHARS:
        amp = find_amp_href(html, final_url)
        if amp:
            amp_html, amp_final = await fetch_html(amp)
            amp_cleaned, amp_title_cands = _preclean_html(amp_html)
            text2 = trafilatura.extract(
                amp_cleaned, include_comments=False, include_images=False,
                favor_recall=True, target_language="id", url=amp_final
            )
            if text2 and len(text2.strip()) > len(text or ""):
                text, final_url, title_cands = text2, amp_final, amp_title_cands
                published_at = _extract_published_at(amp_html) or published_at

    if not text or len(text.strip()) < MIN_TEXT_CHARS:
        raise ValueError("Konten artikel berita terlalu pendek / gagal diekstrak.")

    clean = clean_text_basic(text)
    clean = _postprocess(clean, title_cands)

    title = _pick_best_title(title_cands) or ""
    if not title:
        title = _clean_title(clean[:120])

    host = urlparse(final_url).netloc.lower()
    return ExtractResult(
        text=clean,
        source=host,
        length=len(clean),
        title=title,
        content=clean,
        published_at=published_at,
    )
