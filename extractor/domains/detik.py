import re
from urllib.parse import urlparse
import trafilatura
from bs4 import BeautifulSoup
from ..base import ExtractResult, MIN_TEXT_CHARS, fetch_html, find_amp_href, clean_text_basic

def _preclean_detik_html(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
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

    return str(soup)

def _postprocess_detik(text: str) -> str:
    t = text
    t = re.sub(r'\s*\b[Ff]oto[:：]\s*[^。.!?\n\r|]*?(detik(?:com)?|detik\w+)[^。.!?\n\r|]*\s*\|?', ' ', t)
    t = re.sub(r'\s*\b[Ss]aksikan\b[^:]{0,100}\bdetik\w*[^:]{0,100}:', ' ', t)
    t = re.sub(r'\s*\b[Tt]onton\b[^:]{0,100}\bdetik\w*[^:]{0,100}:', ' ', t)
    t = re.sub(r'\s*\([a-z]{2,4}/[a-z]{2,4}\)\s*(?=$|[.!?]\s*$)', ' ', t, flags=re.IGNORECASE)
    t = re.sub(r'\s*\|\s*', ' ', t)
    t = re.sub(r'\s{2,}', ' ', t).strip()
    return t

async def extract(url: str) -> ExtractResult:
    html, final_url = await fetch_html(url)
    cleaned_html = _preclean_detik_html(html)

    text = trafilatura.extract(
        cleaned_html, include_comments=False, include_images=False,
        favor_recall=True, target_language="id", url=final_url
    )

    if not text or len(text.strip()) < MIN_TEXT_CHARS:
        amp = find_amp_href(html, final_url)
        if amp:
            amp_html, amp_final = await fetch_html(amp)
            amp_cleaned = _preclean_detik_html(amp_html)
            text2 = trafilatura.extract(
                amp_cleaned, include_comments=False, include_images=False,
                favor_recall=True, target_language="id", url=amp_final
            )
            if text2 and len(text2.strip()) > len(text or ""):
                text, final_url = text2, amp_final

    if not text or len(text.strip()) < MIN_TEXT_CHARS:
        raise ValueError("Konten artikel terlalu pendek / gagal diekstrak.")

    clean = clean_text_basic(text)
    clean = _postprocess_detik(clean)

    host = urlparse(final_url).netloc.lower()
    preview = clean
    return ExtractResult(text=clean, source=host, length=len(clean), preview=preview)
