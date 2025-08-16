import re
from urllib.parse import urlparse
import trafilatura
from bs4 import BeautifulSoup
from ..base import ExtractResult, MIN_TEXT_CHARS, fetch_html, find_amp_href, clean_text_basic

# -------- Helpers --------
def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()

def _clean_title(raw: str) -> str:
    t = _norm(raw)

    # buang suffix brand
    t = re.sub(r'\s*[\-|–]\s*(?:[A-Za-z ]+)?\s*Tempo\.co\b.*$', '', t, flags=re.IGNORECASE)
    t = re.sub(r'\s*\|\s*Tempo\.co\b.*$', '', t, flags=re.IGNORECASE)

    # rapikan kutip yang double/longgar
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
    """
    Bersihkan HTML Tempo sebelum di-parse oleh Trafilatura.
    """
    soup = BeautifulSoup(html, "html.parser")

    selectors = [
        "figure", "figcaption", "picture", "iframe", "video", "noscript",
        ".baca-juga", ".lihat-juga", ".related", ".related-articles",
        ".artikel-terkait", ".tag__artikel", ".box_tag", ".tags",
        ".advertisement", ".ads", ".ads__slot", "[data-ad]",
        ".share", ".share-buttons", ".social-share",
        ".pilihan", ".pilihan-tempo", ".credit", ".caption", ".image__caption"
    ]
    for sel in selectors:
        for node in soup.select(sel):
            node.decompose()

    for node in soup.find_all(True):
        txt = _norm(node.get_text(" ", strip=True))
        if not txt:
            continue
        low = txt.lower()
        if low.startswith("baca berita dengan sedikit iklan"):
            node.decompose()
            continue
        if low.startswith("scroll ke bawah untuk melanjutkan membaca"):
            node.decompose()
            continue
        if low.startswith("pilihan"):
            node.decompose()
            continue

    return str(soup)

def _postprocess(text: str) -> str:
    """
    Bersihkan sisa-sisa teks dari Tempo.
    """
    t = _norm(text)

    # Hapus baris CTA iklan
    t = re.sub(
        r'Baca berita dengan sedikit iklan, klik di sini',
        ' ',
        t,
        flags=re.IGNORECASE
    )
    t = re.sub(
        r'Scroll ke bawah untuk melanjutkan membaca.*?(klik di sini)?',
        ' ',
        t,
        flags=re.IGNORECASE
    )

    # Hapus kata 'Pilihan' di akhir artikel berita
    t = re.sub(r'Pilihan\s*$', ' ', t, flags=re.IGNORECASE)

    # Rapikan whitespace
    t = re.sub(r'\s{2,}', ' ', t).strip()
    return t

async def extract(url: str) -> ExtractResult:
    html, final_url = await fetch_html(url)

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
    )
