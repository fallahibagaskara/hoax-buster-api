import re
from urllib.parse import urlparse
import trafilatura
from bs4 import BeautifulSoup, Tag
from ..base import ExtractResult, MIN_TEXT_CHARS, fetch_html, find_amp_href, clean_text_basic

# -------- Helpers --------
def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()

def _strip_prefix(text: str) -> str:
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

def _strip_prefix_in_dom(p: Tag) -> None:
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

def _clean_title(raw: str) -> str:
    t = _norm(raw)
    # hapus suffix brand
    t = re.sub(r'\s*[\-|–]\s*Liputan6\.com\b.*$', '', t, flags=re.IGNORECASE)

    # rapikan kutip yang double/longgar
    t = re.sub(r'^[\'"“”‘’\[\(]+\s*', '', t)
    t = re.sub(r'\s*[\'"“”‘’\]\)]+$', '', t)
    return _norm(t)

def _extract_title_candidates(soup: BeautifulSoup) -> list[str]:
    cands = []

    h1 = soup.select_one("h1.read-page--header--title") or soup.find("h1")
    if h1:
        cands.append(_norm(h1.get_text(" ", strip=True)))

    #  meta og:title / twitter:title
    for m in soup.select("meta[property='og:title'], meta[name='twitter:title']"):
        content = _norm(m.get("content") or "")
        if content:
            cands.append(content)

    if soup.title and soup.title.string:
        cands.append(_norm(soup.title.string))

    seen, uniq = set(), []
    for t in cands:
        k = t.lower()
        if k not in seen and t:
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
        if ct.lower() in ("liputan6", "beranda", "news"):
            continue
        seen.add(k)
        cleaned.append(ct)
    return cleaned[0] if cleaned else None

def _preclean_html(html: str) -> str:
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

    for sel in [
        "script", "ins", "iframe", "figure", "figcaption", "picture",
        ".advertisement", ".advertisement-placeholder", ".article-ad",
        ".seamless-ads", "[data-ad]", ".photo-gateway",
        ".baca-juga", ".baca-juga-collections", ".baca-juga__list",
        ".article-content-body__item-break", 
        ".article-content-body__item-loadmore", 
    ]:
        for n in container.select(sel):
            n.decompose()

    allowed: list[str] = []
    pages = container.select(".article-content-body__item-page")
    if not pages:
        pages = [container]

    for page in pages:
        for section in page.select('.article-content-body__item-content'):
            comp = (section.get("data-component-name") or "")
            if ":section:text" not in comp:
                continue

            for p in section.find_all("p", recursive=True):
                _strip_prefix_in_dom(p)
                txt = _norm(p.get_text(" ", strip=True))
                if not txt or _is_noise_text(txt):
                    continue
                allowed.append(txt)

    if allowed:
        mini = "<article>" + "".join(f"<p>{t}</p>" for t in allowed) + "</article>"
        return mini

    return str(container)

def _postprocess(text: str) -> str:
    t = _norm(text)

    # Strip prefix sumber/kota di awal
    t = _strip_prefix(t)

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
        url=final_url,
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
                url=amp_final,
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
