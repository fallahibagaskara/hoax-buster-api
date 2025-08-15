import re
from urllib.parse import urlparse
import trafilatura
from bs4 import BeautifulSoup
from ..base import ExtractResult, MIN_TEXT_CHARS, fetch_html, find_amp_href, clean_text_basic

def _preclean_cnn_html(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")

    # 1) Hapus blok video/gambar & meta info khas CNN
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

    # 2) Hapus node yang berisi "BREAKING NEWS CNN Indonesia" atau [Gambas:Video CNN]
    for node in soup.find_all(True):
        txt = node.get_text(" ", strip=True)
        if not txt:
            continue
        low = txt.lower()

        # BREAKING NEWS di awal
        if low.startswith("breaking news cnn indonesia"):
            node.decompose()
            continue

        # Node dengan label [Gambas:Video CNN] atau mirip
        if "[gambas:video cnn" in low:
            node.decompose()
            continue

        # "Lihat Juga" di awal node
        if low.startswith("lihat juga"):
            node.decompose()
            continue

    return str(soup)

def _postprocess_cnn(text: str) -> str:
    t = text

    t = re.sub(r'\[Gambar:Video CNN\]', ' ', t, flags=re.IGNORECASE)
    t = re.sub(r'\[Gambas:Video CNN\]', ' ', t, flags=re.IGNORECASE)
    t = re.sub(r'^\s*BREAKING NEWS CNN Indonesia[^\n]*', ' ', t, flags=re.IGNORECASE)
    t = re.sub(r'\bLihat Juga\s*:\s*[^\n]+', ' ', t, flags=re.IGNORECASE)
    t = re.sub(r'\s*\([a-z]{2,4}/[a-z]{2,4}\)\s*(?=$|[.!?]\s*$)', ' ', t, flags=re.IGNORECASE)
    t = re.sub(r'\s*\|\s*', ' ', t)
    t = re.sub(r'\s{2,}', ' ', t).strip()
    return t

async def extract(url: str) -> ExtractResult:
    html, final_url = await fetch_html(url)
    cleaned_html = _preclean_cnn_html(html)

    text = trafilatura.extract(
        cleaned_html, include_comments=False, include_images=False,
        favor_recall=True, target_language="id", url=final_url
    )

    if not text or len(text.strip()) < MIN_TEXT_CHARS:
        amp = find_amp_href(html, final_url)
        if amp:
            amp_html, amp_final = await fetch_html(amp)
            amp_cleaned = _preclean_cnn_html(amp_html)
            text2 = trafilatura.extract(
                amp_cleaned, include_comments=False, include_images=False,
                favor_recall=True, target_language="id", url=amp_final
            )
            if text2 and len(text2.strip()) > len(text or ""):
                text, final_url = text2, amp_final

    if not text or len(text.strip()) < MIN_TEXT_CHARS:
        raise ValueError("Konten artikel terlalu pendek / gagal diekstrak.")

    clean = clean_text_basic(text)
    clean = _postprocess_cnn(clean)

    host = urlparse(final_url).netloc.lower()
    # preview = (clean[:300] + "…") if len(clean) > 300 else clean
    preview = clean
    return ExtractResult(text=clean, source=host, length=len(clean), preview=preview)
