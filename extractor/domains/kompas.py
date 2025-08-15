import re
from urllib.parse import urlparse, urlunparse, urljoin, parse_qs, urlencode
import trafilatura
from bs4 import BeautifulSoup
from ..base import (
    ExtractResult, MIN_TEXT_CHARS,
    fetch_html, find_amp_href, clean_text_basic
)

# ---------- helpers ----------
def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()

def _strip_kompas_prefix(t: str) -> str:
    """
    Hapus pola pembuka seperti:
    'BEKASI, KOMPAS.com - ' atau 'JAKARTA, KOMPAS.com - '
    """
    t = re.sub(
        r'^\s*[A-Z][A-Z\s\.\-/()]{1,40},\s*KOMPAS\.com\s*-\s+',
        '',
        t
    )
    return t

def _collect_read_content(html: str) -> list[str]:
    """
    Ambil paragraf utama dari .read__content, buang 'Baca juga', iklan, widget, dsb.
    Return list paragraf (string) berurutan.
    """
    soup = BeautifulSoup(html, "html.parser")

    # Buang banner Kompas+ dan elemen non-konten lain
    selectors_to_kill = [
        ".gate-kgplus",
        ".read__paging",
        ".kompasidRec",
        ".ads-on-body",
        ".ads-partner-wrap",
        ".advertisement", ".ads", "#ads",
        "iframe", "script", "style",
        ".liftdown_v2_tanda",
        ".inner-link-baca-juga",  # link "Baca juga" di dalam strong
        ".read__byline", ".read__credit", ".read__photo",
    ]
    for sel in selectors_to_kill:
        for n in soup.select(sel):
            n.decompose()

    # Hilangkan node yang isinya persis/awali dengan “Baca juga …”
    for n in soup.find_all(True):
        txt = n.get_text(" ", strip=True)
        if not txt:
            continue
        low = txt.lower()
        if low.startswith("baca juga"):
            n.decompose()

    wrapper = soup.select_one(".read__content")
    if not wrapper:
        # fallback: kadang struktur berbeda
        wrapper = soup

    # Ambil hanya elemen teks utama (p, h2/h3 yang merupakan subjudul di tengah body)
    chunks: list[str] = []
    for node in wrapper.find_all(["p", "h2", "h3"]):
        # skip paragraf kosong/artefak
        txt = _norm(node.get_text(" ", strip=True))
        if not txt:
            continue
        # skip “Baca juga …” yang lolos
        if txt.lower().startswith("baca juga"):
            continue
        # skip residu “Gabung Kompas.com+”
        if "gabung kompas.com+" in txt.lower():
            continue
        chunks.append(txt)

    return chunks

def _rebuild_minimal_html(paras: list[str]) -> str:
    """Bangun HTML minimal berisi <article><p>… agar trafilatura lebih rapi."""
    soup = BeautifulSoup("<article></article>", "html.parser")
    art = soup.article
    for p in paras:
        tag = soup.new_tag("p")
        tag.string = p
        art.append(tag)
    return str(soup)

def _parse_all_page_links(html: str, base_url: str) -> list[str]:
    """
    Deteksi pagination 'Halaman: 1 2 ...' dan kembalikan semua URL halaman.
    Jika tidak ada, return [] (artinya single page).
    """
    soup = BeautifulSoup(html, "html.parser")
    pages = []
    wrap = soup.select_one(".read__paging .paging__wrap")
    if not wrap:
        return pages
    for a in wrap.select("a.paging__link"):
        href = a.get("href")
        if href:
            pages.append(urljoin(base_url, href))
    # Unique + urut sesuai tampil (biasanya sudah)
    seen = set()
    ordered = []
    for u in pages:
        if u not in seen:
            seen.add(u)
            ordered.append(u)
    return ordered

def _ensure_page_param(u: str, page_num: int) -> str:
    """
    Pastikan URL kompas punya ?page=n yang benar (kadang canonical ke ?page=1).
    """
    parsed = urlparse(u)
    q = parse_qs(parsed.query)
    q["page"] = [str(page_num)]
    new_query = urlencode({k: v[0] for k, v in q.items()})
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", new_query, ""))

# ---------- main ----------
async def extract(url: str) -> ExtractResult:
    # 1) Ambil halaman pertama
    html, final_url = await fetch_html(url)

    # 2) Cari pagination → kumpulkan semua halaman
    page_links = _parse_all_page_links(html, final_url)

    # 3) Ambil konten semua halaman (kalau ada). Urut dari 1..N
    all_paras: list[str] = []

    if page_links:
        # Pastikan urut naik, dan kalau page=1 belum ada, tambahkan
        have_p1 = any("page=1" in pl for pl in page_links)
        first = _ensure_page_param(final_url, 1)
        if not have_p1:
            page_links = [first] + page_links

        for pl in page_links:
            h, _ = await fetch_html(pl)
            all_paras.extend(_collect_read_content(h))
    else:
        # Single page
        all_paras.extend(_collect_read_content(html))

    if not all_paras:
        # AMP fallback jika perlu (jarang untuk Kompas)
        amp = find_amp_href(html, final_url)
        if amp:
            amp_html, amp_final = await fetch_html(amp)
            all_paras.extend(_collect_read_content(amp_html))
            final_url = amp_final

    # 4) Build minimal HTML & ekstrak via Trafilatura agar kalimat/spacing rapi
    min_html = _rebuild_minimal_html(all_paras)
    text = trafilatura.extract(
        min_html,
        include_comments=False,
        include_images=False,
        favor_recall=True,
        target_language="id",
        url=final_url,
    )

    # Fallback: kalau trafilatura terlalu hemat, langsung join paragraf
    if not text or len(_norm(text)) < 200:
        text = " ".join(all_paras)

    if not text or len(_norm(text)) < MIN_TEXT_CHARS:
        raise ValueError("Konten artikel terlalu pendek / gagal diekstrak.")

    # 5) Post-clean: buang prefix kota + KOMPAS.com, sisa artefak, whitespace
    clean = clean_text_basic(text)
    clean = _strip_kompas_prefix(clean)
    # Hilangkan sisa “Baca juga …” yang mungkin lolos
    clean = re.sub(r'\bBaca juga\s*:\s*[^\n]+', ' ', clean, flags=re.IGNORECASE)
    # Rapikan pipa/spasi
    clean = re.sub(r'\s*\|\s*', ' ', clean)
    clean = re.sub(r'\s{2,}', ' ', clean).strip()

    host = urlparse(final_url).netloc.lower()
    preview = clean
    return ExtractResult(text=clean, source=host, length=len(clean), preview=preview)
