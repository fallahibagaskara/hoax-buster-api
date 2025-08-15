import re
from urllib.parse import urlparse
import trafilatura
from bs4 import BeautifulSoup
from ..base import ExtractResult, MIN_TEXT_CHARS, fetch_html, find_amp_href, clean_text_basic

# -------- Helpers --------
def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()

def _extract_title_candidates(soup: BeautifulSoup) -> list[str]:
    cands = []

    # h1 (judul)
    h1 = soup.find("h1")
    if h1:
        cands.append(_norm(h1.get_text(" ", strip=True)))

    # variasi class yang dipakai
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

    # dedup sambil pertahankan urutan
    seen = set()
    uniq = []
    for t in cands:
        if t.lower() not in seen:
            seen.add(t.lower())
            uniq.append(t)
    return uniq

def _preclean_cnn_html(html: str) -> tuple[str, list[str]]:
    soup = BeautifulSoup(html, "html.parser")

    # --- hapus blok non-body ---
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

    # buang node “BREAKING NEWS”, “[Gambas:Video CNN]”, “Lihat Juga …”
    for node in soup.find_all(True):
        txt = node.get_text(" ", strip=True)
        if not txt:
            continue
        low = txt.lower()
        if low.startswith("breaking news cnn indonesia") or "[gambas:gambar cnn" or "[gambar:gambar cnn" or "[gambas:video cnn"  or "[gambar:video cnn" in low or low.startswith("lihat juga"):
            node.decompose()

    # ambil kandidat judul SEBELUM soup dirender ke string
    title_candidates = _extract_title_candidates(soup)

    return str(soup), title_candidates

def _strip_leading_title(text: str, title_candidates: list[str]) -> str:
    """
    Hilangkan judul jika nongol di awal teks.
    - Case-insensitive, normalisasi spasi.
    - Toleransi sufiks brand: " - CNN Indonesia".
    - Lindungi agar tidak menghapus kalimat pertama yang bukan judul.
    """
    t = text

    # normalisasi brand suffix
    suffix_patterns = [
        r"\s*-\s*cnn indonesia\b",
        r"\s*\|\s*cnn indonesia\b",
    ]

    for raw_title in title_candidates:
        title = _norm(raw_title)
        if not title or len(title) < 5:  # judul terlalu pendek, skip
            continue

        # pattern: ^judul( - CNN Indonesia)?[,.:–-]?
        # \A untuk anchor awal string setelah trim
        title_re = re.escape(title)
        alt_suffix = "(?:" + "|".join(suffix_patterns) + ")?"
        pattern = r"\A\s*" + title_re + alt_suffix + r"\s*[:\-–,]?\s*"
        new_t, n = re.subn(pattern, "", t, flags=re.IGNORECASE)
        if n > 0:
            t = new_t
            break  # cukup hapus sekali (kandidat pertama yang match)

    return _norm(t)

def _postprocess_cnn(text: str, title_candidates: list[str]) -> str:
    t = text

    # buang artefak multimedia & CTA
    t = re.sub(r'\[Gambar:Gambar CNN\]', ' ', t, flags=re.IGNORECASE)
    t = re.sub(r'\[Gambas:Gambar CNN\]', ' ', t, flags=re.IGNORECASE)
    t = re.sub(r'\[Gambar:Video CNN\]', ' ', t, flags=re.IGNORECASE)
    t = re.sub(r'\[Gambas:Video CNN\]', ' ', t, flags=re.IGNORECASE)
    t = re.sub(r'^\s*BREAKING NEWS CNN Indonesia[^\n]*', ' ', t, flags=re.IGNORECASE)
    t = re.sub(r'\bLihat Juga\s*:\s*[^\n]+', ' ', t, flags=re.IGNORECASE)

    # byline tail (inisial reporter/editor)
    t = re.sub(r'\s*\([a-z]{2,4}/[a-z]{2,4}\)\s*(?=$|[.!?]\s*$)', ' ', t, flags=re.IGNORECASE)

    # strip judul di awal
    t = _strip_leading_title(_norm(t), title_candidates)

    # rapikan
    t = re.sub(r'\s*\|\s*', ' ', t)
    t = re.sub(r'\s{2,}', ' ', t).strip()
    return t

# -------- Main handler --------
async def extract(url: str) -> ExtractResult:
    html, final_url = await fetch_html(url)
    cleaned_html, title_cands = _preclean_cnn_html(html)

    text = trafilatura.extract(
        cleaned_html, include_comments=False, include_images=False,
        favor_recall=True, target_language="id", url=final_url
    )

    if not text or len(text.strip()) < MIN_TEXT_CHARS:
        amp = find_amp_href(html, final_url)
        if amp:
            amp_html, amp_final = await fetch_html(amp)
            amp_cleaned, amp_title_cands = _preclean_cnn_html(amp_html)
            text2 = trafilatura.extract(
                amp_cleaned, include_comments=False, include_images=False,
                favor_recall=True, target_language="id", url=amp_final
            )
            if text2 and len(text2.strip()) > len(text or ""):
                text, final_url, title_cands = text2, amp_final, amp_title_cands

    if not text or len(text.strip()) < MIN_TEXT_CHARS:
        raise ValueError("Konten artikel terlalu pendek / gagal diekstrak.")

    clean = clean_text_basic(text)
    clean = _postprocess_cnn(clean, title_cands)

    host = urlparse(final_url).netloc.lower()
    title = "judul"
    preview = clean
    return ExtractResult(text=clean, source=host, length=len(clean), title=title, preview=preview)
