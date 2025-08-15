# extractor/domains/kumparan.py
import re
from urllib.parse import urlparse
import trafilatura
from bs4 import BeautifulSoup, Tag
from ..base import ExtractResult, MIN_TEXT_CHARS, fetch_html, find_amp_href, clean_text_basic

# ---------- utils ----------
def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()

def _is_noise_text(txt: str) -> bool:
    low = txt.lower()
    return (
        not txt
        or low == "advertisement"
        or low.startswith("live update")
    )

# ---------- DOM preclean ----------
def _preclean_kumparan_html(html: str) -> str:
    """
    Fokus ambil hanya konten paragraf kumparan:
    <span data-qa-id="story-paragraph">...</span>
    Buang: figure/caption, breaking-news, live update, ads, title, share, tags, dsb.
    """
    soup = BeautifulSoup(html, "html.parser")

    # 1) Kill obvious non-content blocks (iklan, figure, caption, breaking/live modules, share, tags)
    selectors = [
        "figure", "figcaption", "picture", "iframe", "video", "svg",
        "[data-qa-id='desktop-article-page-below-images']",
        "[data-qa-id='breaking-news-compartment']",
        "[data-qa-id='breaking-news-compartment-list-container']",
        "[data-qa-id='end_of_article']",
        "[data-qa-id='share-wrapper']",
        "[data-qa-id='story-footer']",
        "[data-qa-id='story-author']",
        "[data-qa-id='publish-date']",
        "[data-qa-id='reading-time']",
        ".Advertisement", ".advertisement", "[aria-label='Advertisement']",
        "[id^='google_ads_iframe_']",
        "[data-qa-id='home-breadcrumb']",
        "[data-qa-id='channel-name']",
        "[data-qa-id='story-title']",
        ".Viewweb__StyledView-sc-b0snvl-0.dFRvmS"  # breadcrumbs container
    ]
    for sel in selectors:
        for node in soup.select(sel):
            node.decompose()

    # 2) Beberapa “ADVERTISEMENT” berupa <span> kecil – sweep berbasis konten
    for node in soup.find_all(True):
        txt = node.get_text(" ", strip=True)
        if txt and txt.strip().upper() == "ADVERTISEMENT":
            node.decompose()

    # 3) Whitelist paragraph spans
    allowed: list[str] = []
    # Kontainer besar renderer (kadang banyak), tapi yang penting paragrafnya
    for sp in soup.select("span[data-qa-id='story-paragraph']"):
        # Make sure ini bukan bagian dari caption/figure yang tersisa
        if sp.find_parent(["figure", "figcaption"]) is not None:
            continue
        txt = _norm(sp.get_text(" ", strip=True))
        if not _is_noise_text(txt):
            allowed.append(txt)

    # 4) Fallback: beberapa artikel pakai paragraph container tanpa data-qa-id?
    # Ambil block .track_paragraph → span[data-qa-id=story-paragraph] ada di dalamnya.
    if not allowed:
        for blk in soup.select(".track_paragraph"):
            sp = blk.find("span", attrs={"data-qa-id": "story-paragraph"})
            if not sp:
                continue
            txt = _norm(sp.get_text(" ", strip=True))
            if not _is_noise_text(txt):
                allowed.append(txt)

    # 5) Rebuild minimal HTML berisi paragraf doang agar trafilatura clean
    # (atau langsung join teks – di sini kita biarkan trafilatura tetap proses)
    wrapper = BeautifulSoup("<article></article>", "html.parser")
    art = wrapper.article
    for p in allowed:
        tag = wrapper.new_tag("p")
        tag.string = p
        art.append(tag)

    return str(wrapper)

# ---------- postprocess ----------
def _postprocess_kumparan(text: str) -> str:
    """
    Bersihkan sisa artefak: ADVERTISEMENT, pipa, spasi dobel, dsb.
    Buang duplikasi judul if somehow lolos (h1 diawali huruf kapital semua, namun kita tidak tarik h1).
    """
    t = _norm(text)
    t = re.sub(r'\bADVERTISEMENT\b', ' ', t, flags=re.IGNORECASE)
    # Beberapa artikel push label editorial di awal: "kumparanNEWS" → jarang, tapi filter aman:
    t = re.sub(r'^\s*kumparan(?:news|bisnis|style|tech|oto|bola)\b[:\s,|-]*', ' ', t, flags=re.IGNORECASE)
    t = re.sub(r'\s*\|\s*', ' ', t)
    t = re.sub(r'\s{2,}', ' ', t).strip()
    return t

# ---------- main ----------
async def extract(url: str) -> ExtractResult:
    html, final_url = await fetch_html(url)
    cleaned_html = _preclean_kumparan_html(html)

    # Trafilatura tetap kita pakai untuk normalisasi umum & jaga-jaga
    text = trafilatura.extract(
        cleaned_html,
        include_comments=False,
        include_images=False,
        favor_recall=True,
        target_language="id",
        url=final_url,
    )

    # Fallback: kalau trafilatura terlalu hemat, langsung join paragraf manual
    if not text or len(_norm(text)) < 200:
        soup2 = BeautifulSoup(cleaned_html, "html.parser")
        chunks = [_norm(p.get_text(" ", strip=True)) for p in soup2.find_all("p")]
        text = " ".join([c for c in chunks if c])

    if not text or len(_norm(text)) < MIN_TEXT_CHARS:
        # Kumparan nyaris tidak pakai AMP, tapi kita cek link amp kalau ada
        amp = find_amp_href(html, final_url)
        if amp:
            amp_html, amp_final = await fetch_html(amp)
            amp_cleaned = _preclean_kumparan_html(amp_html)
            text2 = trafilatura.extract(
                amp_cleaned,
                include_comments=False,
                include_images=False,
                favor_recall=True,
                target_language="id",
                url=amp_final,
            ) or ""
            if len(_norm(text2)) > len(_norm(text or "")):
                text, final_url = text2, amp_final

    if not text or len(_norm(text)) < MIN_TEXT_CHARS:
        raise ValueError("Konten artikel terlalu pendek / gagal diekstrak.")

    clean = clean_text_basic(text)
    clean = _postprocess_kumparan(clean)

    host = urlparse(final_url).netloc.lower()
    preview = clean
    return ExtractResult(text=clean, source=host, length=len(clean), preview=preview)
