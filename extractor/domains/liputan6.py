import re
from urllib.parse import urlparse
import trafilatura
from bs4 import BeautifulSoup
from ..base import ExtractResult, MIN_TEXT_CHARS, fetch_html, find_amp_href, clean_text_basic

# ---------- helpers ----------
def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()

def _preclean_l6_html(html: str) -> str:
    """
    Pre-clean DOM Liputan6 agar Trafilatura tidak ikut menarik
    caption, 'Baca Juga', dan blok iklan.
    """
    soup = BeautifulSoup(html, "html.parser")

    # 1) Hapus elemen media/iklan/tautan terkait umum di Liputan6
    selectors = [
        "figure", "figcaption", "picture", "iframe",
        # blok iklan & promosi
        ".advertisement", ".ads", ".ads__wrapper", ".ads__slot", ".ad__slot", "#ads", "[data-ad]",
        # baca juga/terkait
        ".baca-juga", ".bacajuga", ".lihat-juga", ".related", ".related-articles",
        ".artikel-terkait", ".tag__artikel", ".tag-cloud", ".tagcloud", ".tags",
        ".read__also", ".read__more",
        # share/cta
        ".share", ".share-buttons", ".social-share",
        # kredit/caption
        ".media__caption", ".caption", ".caption__text", ".credit", ".image__caption",
    ]
    for sel in selectors:
        for node in soup.select(sel):
            node.decompose()

    # 2) Hapus node yang text-nya “Advertisement” / “Baca Juga …”
    for node in soup.find_all(True):
        txt = node.get_text(" ", strip=True)
        if not txt:
            continue
        low = txt.lower()
        if low == "advertisement" or low.startswith("baca juga") or low.startswith("lihat juga"):
            node.decompose()

    return str(soup)

def _postprocess_l6(text: str) -> str:
    """
    - Buang prefix 'Liputan6.com, {Kota} - ' atau 'Liputan6.com - ' di awal artikel
    - Hapus sisa 'Advertisement'
    - Rapikan whitespace
    """
    t = _norm(text)

    # Prefix lokasi khas Liputan6 (Jakarta dkk) → "Liputan6.com, Jakarta - "
    t = re.sub(
        r'^\s*Liputan6\.com,\s*[^-]{1,60}-\s+',
        '',
        t,
        flags=re.IGNORECASE
    )
    # Varian tanpa kota → "Liputan6.com - "
    t = re.sub(
        r'^\s*Liputan6\.com\s*-\s+',
        '',
        t,
        flags=re.IGNORECASE
    )

    # Sisa artefak iklan
    t = re.sub(r'\bAdvertisement\b', ' ', t, flags=re.IGNORECASE)

    # Rapikan pipa/spasi
    t = re.sub(r'\s*\|\s*', ' ', t)
    t = re.sub(r'\s{2,}', ' ', t).strip()
    return t

# ---------- main handler ----------
async def extract(url: str) -> ExtractResult:
    html, final_url = await fetch_html(url)
    cleaned_html = _preclean_l6_html(html)

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
            amp_cleaned = _preclean_l6_html(amp_html)
            text2 = trafilatura.extract(
                amp_cleaned,
                include_comments=False,
                include_images=False,
                favor_recall=True,
                target_language="id",
                url=amp_final,
            )
            if text2 and len(text2.strip()) > len(text or ""):
                text, final_url = text2, amp_final

    if not text or len(text.strip()) < MIN_TEXT_CHARS:
        raise ValueError("Konten artikel terlalu pendek / gagal diekstrak.")

    clean = clean_text_basic(text)
    clean = _postprocess_l6(clean)

    host = urlparse(final_url).netloc.lower()
    preview = clean
    return ExtractResult(text=clean, source=host, length=len(clean), preview=preview)
