import re
from urllib.parse import urlparse
import trafilatura
from bs4 import BeautifulSoup, Tag
from datetime import datetime, timezone, timedelta
from ..base import ExtractResult, MIN_TEXT_CHARS, fetch_html, find_amp_href, clean_text_basic

# timezone Indonesia
_WIB = timezone(timedelta(hours=7))
_WITA = timezone(timedelta(hours=8))
_WIT = timezone(timedelta(hours=9))

_MONTH_ID = {
    "januari":1, "februari":2, "maret":3, "april":4, "mei":5, "juni":6,
    "juli":7, "agustus":8, "september":9, "oktober":10, "november":11, "desember":12,
    "jan":1, "jan.":1, "feb":2, "feb.":2, "mar":3, "mar.":3, "apr":4, "apr.":4,
    "mei":5, "jun":6, "jun.":6, "jul":7, "jul.":7, "agu":8, "agu.":8, "ags":8, "ags.":8,
    "sep":9, "sep.":9, "okt":10, "okt.":10, "nov":11, "nov.":11, "des":12, "des.":12,
}

# -------- Helpers --------
def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()

def _to_utc_iso(dt_local: datetime) -> str:
    return dt_local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def _parse_tribun_visible_datetime(text: str) -> datetime | None:
    if not text:
        return None
    t = _norm(text)
    t = re.sub(r'^\s*(tayang|dipublikasikan)\s*:\s*', '', t, flags=re.IGNORECASE)

    m = re.search(
        r'(?:senin|selasa|rabu|kamis|jumat|sabtu|minggu)\s*,\s*'
        r'(\d{1,2})\s+([A-Za-z\.]+)\s+(\d{4})\s+(\d{1,2})[.:](\d{2})\s*(WIB|WITA|WIT)?',
        t, flags=re.IGNORECASE
    )
    if not m:
        m = re.search(
            r'(\d{1,2})\s+([A-Za-z\.]+)\s+(\d{4})\s+(\d{1,2})[.:](\d{2})\s*(WIB|WITA|WIT)?',
            t, flags=re.IGNORECASE
        )
    if not m:
        return None

    dd, mon_name, yy, hh, mi, tz = m.groups()
    mon = _MONTH_ID.get(mon_name.strip().lower())
    if not mon:
        return None
    tzinfo = {"WIB": _WIB, "WITA": _WITA, "WIT": _WIT}.get((tz or "WIB").upper(), _WIB)
    try:
        return datetime(int(yy), int(mon), int(dd), int(hh), int(mi), tzinfo=tzinfo)
    except Exception:
        return None

def _extract_tribun_datetimes(html: str) -> tuple[str | None, str | None]:
    """
    Return (published_at_utc, updated_at_utc) sebagai ISO8601 'YYYY-MM-DDTHH:MM:SSZ'
    """
    soup = BeautifulSoup(html, "html.parser")
    pub = None
    upd = None

    for m in soup.select('meta[property="article:published_time"], meta[itemprop="datePublished"]'):
        val = (m.get("content") or "").strip()
        if not val:
            continue
        try:
            dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
            if not dt.tzinfo:
                dt = dt.replace(tzinfo=_WIB)
            pub = _to_utc_iso(dt)
            break
        except Exception:
            mm = re.search(r'(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?', val)
            if mm:
                y, mo, d, h, mi, ss = mm.groups()
                ss = int(ss) if ss else 0
                try:
                    pub = _to_utc_iso(datetime(int(y), int(mo), int(d), int(h), int(mi), ss, tzinfo=_WIB))
                    break
                except Exception:
                    pass

    for m in soup.select('meta[property="article:modified_time"], meta[itemprop="dateModified"]'):
        val = (m.get("content") or "").strip()
        if not val:
            continue
        try:
            dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
            if not dt.tzinfo:
                dt = dt.replace(tzinfo=_WIB)
            upd = _to_utc_iso(dt)
            break
        except Exception:
            mm = re.search(r'(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?', val)
            if mm:
                y, mo, d, h, mi, ss = mm.groups()
                ss = int(ss) if ss else 0
                try:
                    upd = _to_utc_iso(datetime(int(y), int(mo), int(d), int(h), int(mi), ss, tzinfo=_WIB))
                    break
                except Exception:
                    pass

    if not pub:
        time_el = soup.find("time")
        if time_el:
            span = time_el.find("span")
            raw = span.get_text(" ", strip=True) if span else time_el.get_text(" ", strip=True)
            dt = _parse_tribun_visible_datetime(raw)
            if dt:
                pub = _to_utc_iso(dt)

    if not pub and upd:
        pub = upd

    return pub, upd

NOISE_PREFIXES = (
    "penulis:", "editor:", "laporan wartawan", "baca juga", "lihat juga",
)

def _is_noise_text(text: str) -> bool:
    low = text.lower().strip()
    if not low:
        return True
    if any(low.startswith(p) for p in NOISE_PREFIXES):
        return True
    if re.match(r'^\|\s*[^|]+\s*\|', text):
        return True
    if low in ("advertisement",):
        return True
    return False

# ---------- title helpers ----------
def _clean_title(raw: str) -> str:
    t = _norm(raw)
    t = re.sub(r'\s*[\-|–]\s*(?:[A-Za-z ]+)?\s*Tribun\w+\.com\b.*$', '', t, flags=re.IGNORECASE)
    t = re.sub(r'\s*\|\s*Tribun\w+\.com\b.*$', '', t, flags=re.IGNORECASE)
    t = re.sub(r'\bTRIBUNNEWS\.COM,\s*[^-]{1,60}-\s*', ' ', t, flags=re.IGNORECASE)
    t = re.sub(r'^[\'"“”‘’\[\(]+\s*', '', t)
    t = re.sub(r'\s*[\'"“”‘’\]\)]+$', '', t)
    return _norm(t)

def _extract_title_candidates(soup: BeautifulSoup) -> list[str]:
    cands = []

    h1id = soup.select_one("h1#arttitle")
    if h1id:
        cands.append(_norm(h1id.get_text(" ", strip=True)))

    if not h1id:
        h1 = soup.find("h1")
        if h1:
            cands.append(_norm(h1.get_text(" ", strip=True)))

    #  meta og/twitter title
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
            seen.add(k)
            uniq.append(t)
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
        if ct.lower() in ("tribunnews.com", "tribunbogor.com", "tribunstyle.com", "beranda", "news"):
            continue
        seen.add(k)
        cleaned.append(ct)
    return cleaned[0] if cleaned else None

def _preclean_html(html: str) -> str:
    """
    Whitelist extraction: fokus ambil isi dari container utama Tribun,
    bersihkan iklan & related.
    """
    soup = BeautifulSoup(html, "html.parser")

    container = soup.select_one(".side-article.txt-article.multi-fontsize")
    if not container:
        for sel in ["script", "ins", "iframe", ".ads-placeholder", "[data-ad]"]:
            for n in soup.select(sel):
                n.decompose()
        return str(soup)

    for sel in ["script", "ins", "iframe", ".ads-placeholder", "[data-ad]"]:
        for n in container.select(sel):
            n.decompose()

    allowed_nodes: list[str] = []
    for node in container.find_all(["p", "h2", "h3"], recursive=True):
        if isinstance(node, Tag) and ("baca" in (node.get("class") or [])):
            continue
        text = _norm(node.get_text(" ", strip=True))
        if not text or _is_noise_text(text):
            continue
        allowed_nodes.append(text)

    if allowed_nodes:
        mini = "<article>" + "".join(f"<p>{t}</p>" for t in allowed_nodes) + "</article>"
        return mini

    for sel in ["script", "ins", "iframe", ".ads-placeholder", "[data-ad]"]:
        for n in soup.select(sel):
            n.decompose()
    return str(soup)

def _postprocess(text: str) -> str:
    t = _norm(text)
    t = re.sub(r'^\s*TRIBUN\w+\.COM,\s*[^-]{1,80}-\s*', ' ', t, flags=re.IGNORECASE)
    t = re.sub(r'^\s*[A-Za-zÀ-ÿ .\'-]+,\s*TRIBUN\w+\.COM\s*[—–-]{1,2}\s*', ' ', t, flags=re.IGNORECASE)
    t = re.sub(r'^\s*TRIBUN\w+\.COM\s*[—–-]{1,2}\s*', ' ', t, flags=re.IGNORECASE)
    t = re.sub(r'\bTRIBUNNEWS\.COM,\s*[^-]{1,60}-\s*', ' ', t, flags=re.IGNORECASE)
    t = re.sub(r'\bBaca juga\s*:\s*[^\n]+', ' ', t, flags=re.IGNORECASE)
    t = re.sub(r'\bLihat juga\s*:\s*[^\n]+', ' ', t, flags=re.IGNORECASE)
    t = re.sub(r'\bPenulis:\s*[^\n]+', ' ', t, flags=re.IGNORECASE)
    t = re.sub(r'\bEditor:\s*[^\n]+', ' ', t, flags=re.IGNORECASE)
    t = re.sub(r'\bLaporan Wartawan\s+[^\n]+', ' ', t, flags=re.IGNORECASE)
    t = re.sub(r'\|\s*[^|]+\s*\|\s*(\|---\|\s*\|\s*[^|]+\s*\|)+', ' ', t)
    t = re.sub(r'\s{2,}', ' ', t).strip()
    return t

async def extract(url: str) -> ExtractResult:
    html, final_url = await fetch_html(url)

    published_at, updated_at = _extract_tribun_datetimes(html)

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

    if not text or len(text.strip()) < MIN_TEXT_CHARS:
        amp = find_amp_href(html, final_url)
        if amp:
            amp_html, amp_final = await fetch_html(amp)

            if not published_at or not updated_at:
                p2, u2 = _extract_tribun_datetimes(amp_html)
                published_at = published_at or p2
                updated_at = updated_at or u2

            amp_cleaned = _preclean_html(amp_html)
            text2 = trafilatura.extract(
                amp_cleaned,
                include_comments=False,
                include_images=False,
                favor_recall=True,
                target_language="id",
                url=amp_final
            )
            if text2 and len(text2.strip()) > len(text or ""):
                text, final_url = text2, amp_final

    if not text or len(text.strip()) < MIN_TEXT_CHARS:
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
