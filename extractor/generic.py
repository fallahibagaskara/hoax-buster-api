from urllib.parse import urlparse
import trafilatura
from .base import ExtractResult, MIN_TEXT_CHARS, fetch_html, find_amp_href, clean_text_basic

async def extract_generic(url: str, source_host: str | None = None) -> ExtractResult:
    html, final_url = await fetch_html(url)

    text = trafilatura.extract(
        html, include_comments=False, include_images=False,
        favor_recall=True, target_language="id", url=final_url
    )

    if not text or len(text.strip()) < MIN_TEXT_CHARS:
        amp = find_amp_href(html, final_url)
        if amp:
            amp_html, amp_final = await fetch_html(amp)
            text2 = trafilatura.extract(
                amp_html, include_comments=False, include_images=False,
                favor_recall=True, target_language="id", url=amp_final
            )
            if text2 and len(text2.strip()) > len(text or ""):
                text, final_url = text2, amp_final

    if not text or len(text.strip()) < MIN_TEXT_CHARS:
        raise ValueError("Konten artikel berita terlalu pendek / gagal diekstrak.")

    clean = clean_text_basic(text)
    host = source_host or urlparse(final_url).netloc.lower()
    title = "judul"
    content = clean
    return ExtractResult(text=clean, source=host, length=len(clean), title=title, content=content)
