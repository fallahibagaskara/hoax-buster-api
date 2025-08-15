import re
from urllib.parse import urlparse
import trafilatura
from bs4 import BeautifulSoup
from ..base import ExtractResult, MIN_TEXT_CHARS, fetch_html, find_amp_href, clean_text_basic

def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()

# ---------- title helpers ----------
def _clean_title_tempo(raw: str) -> str:
    t = _norm(raw)
    # buang suffix brand/kanal, contoh:
    # "Mahkamah ... - Nasional Tempo.co" / " | Tempo.co"
    t = re.sub(r'\s*[\-|–]\s*(?:[A-Za-z ]+)?\s*Tempo\.co\b.*$', '', t, flags=re.IGNORECASE)
    t = re.sub(r'\s*\|\s*Tempo\.co\b.*$', '', t, flags=re.IGNORECASE)
    # rapikan kutip/kurung pinggir
    t = re.sub(r'^[\'"“”‘’\[\(]+\s*', '', t)
    t = re.sub(r'\s*[\'"“”‘’\]\)]+$', '', t)
    return _norm(t)

def _extract_title_candidates_tempo(soup: BeautifulSoup) -> list[str]:
    cands = []
    # 1) h1 utama (Tempo Next: h1.text-[26px] ...)
    h1 = soup.find("h1")  # cukup umum, biasanya satu
    if h1:
        cands.append(_norm(h1.get_text(" ", strip=True)))
    # 2) meta og:title / twitter:title
    for m in soup.select("meta[property='og:title'], meta[name='twitter:title']"):
        content = _norm(m.get("content") or "")
        if content:
            cands.append(content)
    # 3) <title>
    if soup.title and soup.title.string:
        cands.append(_norm(soup.title.string))
    # dedup (case-insensitive, pertahankan urutan)
    seen, uniq = set(), []
    for t in cands:
        k = t.lower()
        if t and k not in seen:
            seen.add(k)
            uniq.append(t)
    return uniq

def _pick_best_title_tempo(cands: list[str]) -> str | None:
    BEST_MIN_LEN = 6
    seen = set()
    cleaned = []
    for c in cands:
        ct = _clean_title_tempo(c)
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

def _preclean_tempo_html(html: str) -> str:
    """
    Bersihkan HTML Tempo sebelum di-parse oleh Trafilatura.
    """
    soup = BeautifulSoup(html, "html.parser")

    # Hapus elemen media, iklan, baca juga, pilihan, dll
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

    # Hapus node yang text-nya hanya iklan atau call-to-action
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

def _postprocess_tempo(text: str) -> str:
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

    # --- ambil judul dari HTML asli (paling akurat)
    soup_title = BeautifulSoup(html, "html.parser")
    title_cands = _extract_title_candidates_tempo(soup_title)
    title = _pick_best_title_tempo(title_cands) or ""

    cleaned_html = _preclean_tempo_html(html)

    text = trafilatura.extract(
        cleaned_html,
        include_comments=False,
        include_images=False,
        favor_recall=True,
        target_language="id",
        url=final_url
    )
    # ... (bagian AMP & fallback tetap sama)

    clean = clean_text_basic(text)
    clean = _postprocess_tempo(clean)

    host = urlparse(final_url).netloc.lower()
    return ExtractResult(
        text=clean,
        source=host,
        length=len(clean),
        title=title if title else _clean_title_tempo(clean[:120]),
        content=clean,
    )
