import re
from urllib.parse import urlparse
import trafilatura
from bs4 import BeautifulSoup, Tag
from ..base import ExtractResult, MIN_TEXT_CHARS, fetch_html, find_amp_href, clean_text_basic

# ---------- helpers ----------
def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()

def _strip_prefix_liputan6(text: str) -> str:
    """
    Hapus prefix:
      1) "Liputan6.com, {Kota(1-3 kata kapital)} - "
      2) "Liputan6.com, {Kota(1-3 kata kapital)} "   (tanpa '-')
      3) "Liputan6.com - "                           (tanpa kota)
    Tidak akan menelan kata pertama kalimat seperti "Rencana".
    """
    t = _norm(text)

    # 1) Dengan kota + dash (contoh: "Liputan6.com, Jakarta - ")
    t_new = re.sub(
        r'^\s*Liputan6\.com,\s*(?:[A-ZÀ-ÖØ-Ý][\w’\'\.-]+(?:\s+[A-ZÀ-ÖØ-Ý][\w’\'\.-]+){0,2})\s*-\s+',
        '',
        t,
        flags=re.IGNORECASE
    )
    if t_new != t:
        return t_new

    # 2) Dengan kota tanpa dash (contoh: "Liputan6.com, Jakarta ")
    t_new = re.sub(
        r'^\s*Liputan6\.com,\s*(?:[A-ZÀ-ÖØ-Ý][\w’\'\.-]+(?:\s+[A-ZÀ-ÖØ-Ý][\w’\'\.-]+){0,2})\s+',
        '',
        t,
        flags=re.IGNORECASE
    )
    if t_new != t:
        return t_new

    # 3) Tanpa kota (contoh: "Liputan6.com - ")
    t = re.sub(
        r'^\s*Liputan6\.com\s*-\s+',
        '',
        t,
        flags=re.IGNORECASE
    )
    return t

def _strip_l6_prefix_in_dom(p: Tag) -> None:
    """
    Hapus prefix 'Liputan6.com, {Kota}[ -]' yang sering ditaruh
    di dalam <b> atau <strong> pada awal paragraf.
    Operasi pada DOM agar tidak memangkas kata pertama isi.
    """
    # hanya proses kalau child pertama bold/strong dan mengandung 'Liputan6.com'
    first = p.find(True, recursive=False)  # hanya anak langsung
    if first and first.name in ("b", "strong"):
        txt = _norm(first.get_text(" ", strip=True))
        if txt.lower().startswith("liputan6.com"):
            # hapus elemen bold/strong itu seluruhnya
            first.decompose()
            # bersihkan sisa spasi/koma/strip yatim di awal paragraf
            # contoh: ", Jakarta - " yang tertinggal sebagai teks
            if p.contents and isinstance(p.contents[0], str):
                p.contents[0].replace_with(
                    re.sub(r'^\s*[,:\-–]\s*', ' ', str(p.contents[0]))
                )

def _is_noise_text(text: str) -> bool:
    low = text.lower().strip()
    if not low:
        return True
    if low == "advertisement":
        return True
    if low.startswith("baca juga"):
        return True
    if low.startswith("lihat juga"):
        return True
    if low.startswith("selanjutnya:"):
        return True
    return False

def _preclean_l6_html(html: str) -> str:
    """
    Whitelist extraction di container .article-content-body:
    - Ambil hanya section text (p) antar halaman.
    - Buang ads/galeri/paging/baca-juga.
    """
    soup = BeautifulSoup(html, "html.parser")

    container = soup.select_one(".article-content-body")
    if not container:
        # fallback: bersihkan kasar lalu serahkan ke trafilatura
        for sel in [
            "script", "ins", "iframe", "figure", "figcaption", "picture",
            ".advertisement", ".advertisement-placeholder", ".article-ad",
            ".seamless-ads", "[data-ad]", ".baca-juga", ".baca-juga-collections",
            ".read__also", ".read__more", ".photo-gateway"
        ]:
            for n in soup.select(sel):
                n.decompose()
        return str(soup)

    # 1) Singkirkan elemen noise di dalam container
    for sel in [
        "script", "ins", "iframe", "figure", "figcaption", "picture",
        ".advertisement", ".advertisement-placeholder", ".article-ad",
        ".seamless-ads", "[data-ad]", ".photo-gateway",
        ".baca-juga", ".baca-juga-collections", ".baca-juga__list",
        ".article-content-body__item-break",  # "x dari y halaman"
        ".article-content-body__item-loadmore",  # "Selanjutnya: ..."
    ]:
        for n in container.select(sel):
            n.decompose()

    # 2) Kumpulkan hanya konten text section dari tiap page (termasuk _hidden)
    allowed: list[str] = []
    pages = container.select(".article-content-body__item-page")
    if not pages:
        pages = [container]  # fallback

    for page in pages:
        for section in page.select('.article-content-body__item-content'):
            comp = (section.get("data-component-name") or "")
            if ":section:text" not in comp:
                continue
            # ambil paragraf
            for p in section.find_all("p", recursive=True):
                _strip_l6_prefix_in_dom(p)  # <--- potong prefix di level DOM
                txt = _norm(p.get_text(" ", strip=True))
                if not txt or _is_noise_text(txt):
                    continue
                allowed.append(txt)

    if allowed:
        mini = "<article>" + "".join(f"<p>{t}</p>" for t in allowed) + "</article>"
        return mini

    # fallback kalau tidak ada allowed
    return str(container)

def _postprocess_l6(text: str) -> str:
    t = _norm(text)

    # Strip prefix sumber/kota di awal
    t = _strip_prefix_liputan6(t)

    # Sisa artefak “BACA JUGA:” yang lolos
    t = re.sub(r'\bBACA JUGA\s*:?\s*[^\n]+', ' ', t, flags=re.IGNORECASE)
    t = re.sub(r'\bLihat Juga\s*:?\s*[^\n]+', ' ', t, flags=re.IGNORECASE)

    # “Selanjutnya: …”
    t = re.sub(r'\bSelanjutnya\s*:\s*[^\n]+', ' ', t, flags=re.IGNORECASE)

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
    title = "judul"
    preview = clean
    return ExtractResult(text=clean, source=host, length=len(clean), title=title, preview=preview)
