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

def _parse_visible_datetime(text: str) -> datetime | None:
    """
    Contoh yang didukung:
    - '17 Agustus 2025 | 15.00 WIB'
    - '17 Agustus 2025, 15:00 WIB'
    - '17 Agustus 2025 15.00 WIB'   (tanpa separator)
    - 'Minggu, 17 Agustus 2025 | 15.00 WIB' (dengan nama hari)
    """
    if not text:
        return None
    t = _norm(text)
    t = re.sub(r'^\s*(diperbarui|dipublikasikan)\s*[:\-]?\s*', '', t, flags=re.IGNORECASE)

    pat = (
        r'^(?:senin|selasa|rabu|kamis|jumat|sabtu|minggu)\s*,?\s*'  
        r'(?P<d>\d{1,2})\s+(?P<mon>[A-Za-z\.]+)\s+(?P<y>\d{4})'
        r'(?:\s*[|,\-]?\s*|\s+)'                                  
        r'(?P<h>\d{1,2})[.:](?P<m>\d{2})(?:[.:](?P<s>\d{2}))?'     
        r'(?:\s*(?P<tz>WIB|WITA|WIT))?'                            
    )
    m = re.search(pat, t, flags=re.IGNORECASE)
    if not m:
        pat2 = (
            r'(?P<d>\d{1,2})\s+(?P<mon>[A-Za-z\.]+)\s+(?P<y>\d{4})'
            r'(?:\s*[|,\-]?\s*|\s+)'
            r'(?P<h>\d{1,2})[.:](?P<m>\d{2})(?:[.:](?P<s>\d{2}))?'
            r'(?:\s*(?P<tz>WIB|WITA|WIT))?'
        )
        m = re.search(pat2, t, flags=re.IGNORECASE)
        if not m:
            return None

    dd   = int(m.group('d'))
    monn = (m.group('mon') or '').strip().lower()
    yy   = int(m.group('y'))
    hh   = int(m.group('h'))
    mi   = int(m.group('m'))
    ss   = int(m.group('s') or 0)
    tz   = (m.group('tz') or 'WIB').upper()

    mon = _MONTH_ID.get(monn)
    if not mon:
        return None
    tzinfo = {'WIB': _WIB, 'WITA': _WITA, 'WIT': _WIT}.get(tz, _WIB)
    try:
        return datetime(yy, int(mon), dd, hh, mi, ss, tzinfo=tzinfo)
    except Exception:
        return None

def _extract_datetimes(html: str) -> tuple[str | None, str | None]:
    """
    Return (published_at_utc, updated_at_utc) sebagai ISO8601 'YYYY-MM-DDTHH:MM:SSZ'
    Prioritas:
      1) meta published/modified
      2) visible (time[datetime] atau text di beberapa node kandidat)
      3) fallback: gunakan modified bila published kosong
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
            pub = _to_utc_iso(dt); break
        except Exception:
            mm = re.search(r'(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?', val)
            if mm:
                y, mo, d, h, mi, ss = mm.groups()
                ss = int(ss) if ss else 0
                try:
                    pub = _to_utc_iso(datetime(int(y), int(mo), int(d), int(h), int(mi), ss, tzinfo=_WIB)); break
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
            upd = _to_utc_iso(dt); break
        except Exception:
            mm = re.search(r'(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?', val)
            if mm:
                y, mo, d, h, mi, ss = mm.groups()
                ss = int(ss) if ss else 0
                try:
                    upd = _to_utc_iso(datetime(int(y), int(mo), int(d), int(h), int(mi), ss, tzinfo=_WIB)); break
                except Exception:
                    pass

    if not pub:
        for tnode in soup.select('time[datetime]'):
            iso = (tnode.get('datetime') or '').strip()
            if not iso:
                continue
            try:
                dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
                if not dt.tzinfo:
                    dt = dt.replace(tzinfo=_WIB)
                pub = _to_utc_iso(dt); break
            except Exception:
                continue

    if not pub:
        candidates = soup.select(
            '.text-neutral-900.text-sm, .text-neutral-800.text-xs, p, span'
        )
        for n in candidates:
            txt = _norm(n.get_text(" ", strip=True))
            if not txt:
                continue
            if 'wib' not in txt.lower() and 'wit' not in txt.lower() and 'wita' not in txt.lower():
                continue
            dt = _parse_visible_datetime(txt)
            if dt:
                pub = _to_utc_iso(dt); break

    if not pub and upd:
        pub = upd

    return pub, upd

_PROMO_PHRASES = [
    r"Akses edisi mingguan dari Tahun 1971",
    r"Akses penuh seluruh artikel Tempo\+",
    r"Baca dengan lebih sedikit gangguan iklan",
    r"Fitur baca cepat di edisi Mingguan",
    r"Anda Mendukung Independensi Jurnalisme Tempo",
]

def _is_promo_text(txt: str) -> bool:
    if not txt:
        return False
    low = txt.strip()
    for pat in _PROMO_PHRASES:
        if re.search(pat, low, flags=re.IGNORECASE):
            return True
    return False

def _clean_title(raw: str) -> str:
    t = _norm(raw)
    t = re.sub(r'\s*[\-|–]\s*(?:[A-Za-z ]+)?\s*Tempo\.co\b.*$', '', t, flags=re.IGNORECASE)
    t = re.sub(r'\s*\|\s*Tempo\.co\b.*$', '', t, flags=re.IGNORECASE)
    t = re.sub(r'^[\'"“”‘’\[\(]+\s*', '', t)
    t = re.sub(r'\s*[\'"“”‘’\]\)]+$', '', t)
    return _norm(t)

def _extract_title_candidates(soup: BeautifulSoup) -> list[str]:
    cands = []

    h1 = soup.find("h1")
    if h1:
        cands.append(_norm(h1.get_text(" ", strip=True)))
    
    # meta og:title / twitter:title
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
        if ct.lower() in ("tempo.co", "beranda", "news"):
            continue
        seen.add(k)
        cleaned.append(ct)
    return cleaned[0] if cleaned else None

def _preclean_html(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")

    selectors = [
        "figure", "figcaption", "picture", "iframe", "video", "noscript",
        ".baca-juga", ".lihat-juga", ".related", ".related-articles",
        ".artikel-terkait", ".tag__artikel", ".box_tag", ".tags",
        ".advertisement", ".ads", ".ads__slot", "[data-ad]",
        ".share", ".share-buttons", ".social-share",
        ".pilihan", ".pilihan-tempo", ".credit", ".caption", ".image__caption",
        "ul", "ol", ".subscription", ".paywall", ".tempo-plus", ".member-box"
    ]
    for sel in selectors:
        for node in soup.select(sel):
            text = _norm(node.get_text(" ", strip=True))
            if _is_promo_text(text):
                node.decompose()

    for node in list(soup.find_all(True)):
        txt = _norm(node.get_text(" ", strip=True))
        if _is_promo_text(txt):
            parent = node
            for _ in range(3):
                if parent and parent.parent and parent.parent.name not in ("html","body"):
                    parent = parent.parent
            try:
                parent.decompose()
            except Exception:
                node.decompose()

    return str(soup)

def _postprocess(text: str) -> str:
    t = _norm(text)
    t = re.sub(r'Baca berita dengan sedikit iklan, klik di sini', ' ', t, flags=re.IGNORECASE)
    t = re.sub(r'Scroll ke bawah untuk melanjutkan membaca.*?(klik di sini)?', ' ', t, flags=re.IGNORECASE)
    t = re.sub(r'Pilihan\s*$', ' ', t, flags=re.IGNORECASE)
    for pat in _PROMO_PHRASES:
        t = re.sub(pat, ' ', t, flags=re.IGNORECASE)
    t = re.sub(r'\s{2,}', ' ', t).strip()
    return t

async def extract(url: str) -> ExtractResult:
    html, final_url = await fetch_html(url)

    published_at, updated_at = _extract_datetimes(html)

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
        url=final_url
    )

    if not text or len(text.strip()) < MIN_TEXT_CHARS:
        amp = find_amp_href(html, final_url)
        if amp:
            amp_html, amp_final = await fetch_html(amp)

            if not published_at or not updated_at:
                p2, u2 = _extract_datetimes(amp_html)
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
