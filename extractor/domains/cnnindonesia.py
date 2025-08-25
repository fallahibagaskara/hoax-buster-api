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
    t = re.sub(r'^\s*(breaking\s+news\s*:\s*|breaking\s+news\s+cnn\s+indonesia\s*[:-]?\s*)',
               '', t, flags=re.IGNORECASE)
    t = re.sub(r'\s*[-|–]\s*cnn indonesia\b.*$', '', t, flags=re.IGNORECASE)
    t = re.sub(r'\s*(\||-)\s*cnn indonesia\b.*$', '', t, flags=re.IGNORECASE)
    t = re.sub(r'^[\'"“”‘’\[\(]+\s*', '', t)
    t = re.sub(r'\s*[\'"“”‘’\]\)]+$', '', t)

    return _norm(t)

def _pick_best_title(cands: list[str]) -> str | None:
    """
    Prioritas: h1, lalu og:title/twitter:title.
    Saring karakter yang terlalu pendek/generic, bersihkan brand suffix.
    """
    seen = set()
    BEST_MIN_LEN = 8

    cleaned = []
    for c in cands:
        ct = _clean_title(c)
        key = ct.lower()
        if not ct or len(ct) < BEST_MIN_LEN:
            continue
        if key in seen:
            continue
        if ct.lower() in ("cnn indonesia", "beranda", "news"):
            continue
        seen.add(key)
        cleaned.append(ct)

    return cleaned[0] if cleaned else None

def _extract_title_candidates(soup: BeautifulSoup) -> list[str]:
    cands = []

    h1 = soup.find("h1")
    if h1:
        cands.append(_norm(h1.get_text(" ", strip=True)))

    for sel in [".title", ".detail__title", ".article__title", ".headline", ".judul"]:
        for node in soup.select(sel):
            t = _norm(node.get_text(" ", strip=True))
            if t:
                cands.append(t)

    # meta og:title / name=title
    for meta in soup.find_all("meta"):
        prop = (meta.get("property") or meta.get("name") or "").lower()
        if prop in ("og:title", "twitter:title", "title"):
            content = _norm(meta.get("content") or "")
            if content:
                cands.append(content)

    seen = set()
    uniq = []
    for t in cands:
        if t.lower() not in seen:
            seen.add(t.lower())
            uniq.append(t)
    return uniq

def _to_utc_iso(dt_local: datetime) -> str:
    return dt_local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def _parse_visible_datetime(soup: BeautifulSoup) -> str | None:
    """
    Cari elemen tanggal:
    <div class="text-cnn_grey text-sm mb-4"> Minggu, 17 Agu 2025 15:40 WIB </div>
    """

    for node in soup.select("div.text-cnn_grey, .text-cnn_grey"):
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
            dt_wib = datetime(year=yyyy, month=int(mon), day=dd, hour=hh, minute=mm, tzinfo=tzinfo)
            return _to_utc_iso(dt_wib)
        except Exception:
            continue
    return None

def _parse_meta_datetime(soup: BeautifulSoup) -> str | None:
    """
    Cek meta tag standar kalau ada:
      - <meta property="article:published_time" content="2025-08-17T08:40:00+07:00">
      - <meta itemprop="datePublished" content="...">
      - <meta name="pubdate" content="...">
    """
    metas = soup.select('meta[property="article:published_time"], meta[itemprop="datePublished"], meta[name="pubdate"]')
    for m in metas:
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

def _extract_published_at(html: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    dt = _parse_meta_datetime(soup)
    if dt:
        return dt
    return _parse_visible_datetime(soup)

def _preclean_html(html: str) -> tuple[str, list[str]]:
    soup = BeautifulSoup(html, "html.parser")

    # hapus blok non-body
    selectors = [
        "figure", "figcaption",
        ".media_artikel", ".media__caption",
        ".detail__media", ".parallax__caption",
        ".lihat-juga", ".read__also", ".artikel__terkait",
        ".topik__terkait", ".box_tag", ".tag__artikel",
        ".breaking_news", ".video__wrapper",
    ]
    for sel in selectors:
        for node in soup.select(sel):
            node.decompose()

    for node in soup.find_all(True):
        txt = node.get_text(" ", strip=True)
        if not txt:
            continue
        low = txt.lower()
        if low.startswith("breaking news cnn indonesia"):
            node.decompose(); continue
        if ("[gambas:video cnn" in low) or ("[gambar:video cnn" in low) \
           or ("[gambas:gambar cnn" in low) or ("[gambar:gambar cnn" in low):
            node.decompose(); continue
        if low.startswith("lihat juga"):
            node.decompose(); continue

    title_candidates = _extract_title_candidates(soup)
    return str(soup), title_candidates

def _strip_leading_title(text: str, title_candidates: list[str]) -> str:
    """
    Hilangkan judul jika ada di awal teks.
    - Case-insensitive, normalisasi spasi.
    - Toleransi suffix brand: " - CNN Indonesia".
    - Lindungi kalimat pertama yang bukan judul.
    """
    t = text

    # normalisasi brand suffix
    suffix_patterns = [
        r"\s*-\s*cnn indonesia\b",
        r"\s*\|\s*cnn indonesia\b",
    ]

    for raw_title in title_candidates:
        title = _norm(raw_title)
        if not title or len(title) < 5:
            continue

        # pattern: ^judul( - CNN Indonesia)?[,.:–-]?
        # \A untuk anchor awal string setelah trim
        title_re = re.escape(title)
        alt_suffix = "(?:" + "|".join(suffix_patterns) + ")?"
        pattern = r"\A\s*" + title_re + alt_suffix + r"\s*[:\-–,]?\s*"
        new_t, n = re.subn(pattern, "", t, flags=re.IGNORECASE)
        if n > 0:
            t = new_t
            break

    return _norm(t)

def _strip_dateline(t: str) -> str:
    t = re.sub(
        r'^\s*(?:cnn\s*indonesia\s*)?(?:[,•-]?\s*)?'
        r'(?:senin|selasa|rabu|kamis|jumat|sabtu|minggu)\s*,?\s*'
        r'\d{1,2}\s+\w+\s+\d{4}\s+\d{1,2}:\d{2}\s*wib\s*',
        ' ',
        t, flags=re.IGNORECASE
    )
    t = re.sub(
        r'^\s*[A-Za-zÀ-ÿ .\'-]+,\s*cnn\s*indonesia\s*[—–-]{1,2}\s*',
        ' ',
        t, flags=re.IGNORECASE
    )
    t = re.sub(
        r'^\s*(?:.*?wib\s+)?[A-Za-zÀ-ÿ .\'-]+,\s*cnn\s*indonesia\s*[—–-]{1,2}\s*',
        ' ',
        t, flags=re.IGNORECASE
    )
    t = re.sub(
        r'^\s*cnn\s*indonesia\s*[—–-]{1,2}\s*',
        ' ',
        t, flags=re.IGNORECASE
    )
    return t

def _postprocess(text: str, title_candidates: list[str]) -> str:
    t = text
    t = _strip_dateline(_norm(t))
    t = re.sub(r'\[Gambar:Gambar CNN\]', ' ', t, flags=re.IGNORECASE)
    t = re.sub(r'\[Gambas:Gambar CNN\]', ' ', t, flags=re.IGNORECASE)
    t = re.sub(r'\[Gambar:Video CNN\]', ' ', t, flags=re.IGNORECASE)
    t = re.sub(r'\[Gambas:Video CNN\]', ' ', t, flags=re.IGNORECASE)
    t = re.sub(r'^\s*BREAKING NEWS CNN Indonesia[^\n]*', ' ', t, flags=re.IGNORECASE)
    t = re.sub(r'\bLihat Juga\s*:\s*[^\n]+', ' ', t, flags=re.IGNORECASE)
    t = re.sub(r'\s*\([a-z]{2,5}/[a-z]{2,5}\)\s*', ' ', t, flags=re.IGNORECASE)
    t = _strip_leading_title(_norm(t), title_candidates)
    t = re.sub(
        r'\b(TOPIK\s+TERKAIT|ARTIKEL\s+TERKAIT|TERKAIT\s+LAINNYA\s+DI\s+DETIKNETWORK)\b.*$',
        ' ',
        t, flags=re.IGNORECASE | re.DOTALL
    )
    t = re.sub(r'\s*\|\s*', ' ', t)
    t = re.sub(r'\s{2,}', ' ', t).strip()
    return t

# -------- Main handler --------
async def extract(url: str) -> ExtractResult:
    html, final_url = await fetch_html(url)
    cleaned_html, title_cands = _preclean_html(html)

    published_at = _extract_published_at(html)

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