import re
from urllib.parse import urlparse
import trafilatura
from bs4 import BeautifulSoup
from ..base import ExtractResult, MIN_TEXT_CHARS, fetch_html, find_amp_href, clean_text_basic

def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()

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

    # Hapus kata 'Pilihan' di akhir artikel
    t = re.sub(r'Pilihan\s*$', ' ', t, flags=re.IGNORECASE)

    # Rapikan whitespace
    t = re.sub(r'\s{2,}', ' ', t).strip()
    return t

async def extract(url: str) -> ExtractResult:
    html, final_url = await fetch_html(url)
    cleaned_html = _preclean_tempo_html(html)

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
            amp_cleaned = _preclean_tempo_html(amp_html)
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
        raise ValueError("Konten artikel terlalu pendek / gagal diekstrak.")

    clean = clean_text_basic(text)
    clean = _postprocess_tempo(clean)

    host = urlparse(final_url).netloc.lower()
    preview = clean
    return ExtractResult(text=clean, source=host, length=len(clean), preview=preview)
