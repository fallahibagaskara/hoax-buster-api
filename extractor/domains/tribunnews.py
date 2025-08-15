import re
from urllib.parse import urlparse
import trafilatura
from bs4 import BeautifulSoup, Tag
from ..base import ExtractResult, MIN_TEXT_CHARS, fetch_html, find_amp_href, clean_text_basic

def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()

NOISE_PREFIXES = (
    "penulis:", "editor:", "laporan wartawan", "baca juga", "lihat juga",
)

def _is_noise_text(text: str) -> bool:
    low = text.lower().strip()
    if not low:
        return True
    if any(low.startswith(p) for p in NOISE_PREFIXES):
        return True
    # ⚠️ JANGAN drop 'tribunnews.com' di sini — akan di-strip per paragraf
    if re.match(r'^\|\s*[^|]+\s*\|', text):  # blok related markdown
        return True
    if low in ("advertisement",):
        return True
    return False

def _preclean_tribun_html(html: str) -> str:
    """
    Whitelist extraction: fokus ambil isi dari container utama Tribun,
    bersihkan iklan & related tanpa menyapu isi.
    """
    soup = BeautifulSoup(html, "html.parser")

    container = soup.select_one(".side-article.txt-article.multi-fontsize")
    if not container:
        # fallback: tetap pakai seluruh dokumen, tapi buang placeholder iklan jelas
        for sel in ["script", "ins", "iframe", ".ads-placeholder", "[data-ad]"]:
            for n in soup.select(sel):
                n.decompose()
        return str(soup)

    # 1) Buang node iklan/placeholder di dalam container
    for sel in ["script", "ins", "iframe", ".ads-placeholder", "[data-ad]"]:
        for n in container.select(sel):
            n.decompose()

    # 2) Kumpulkan node yang diizinkan: p, h2, h3 (header subbagian kadang berguna)
    allowed_nodes: list[str] = []
    for node in container.find_all(["p", "h2", "h3"], recursive=True):
        # skip paragraf "baca juga" berbasis class
        if isinstance(node, Tag) and ("baca" in (node.get("class") or [])):
            continue
        text = _norm(node.get_text(" ", strip=True))
        if not text or _is_noise_text(text):
            continue
        allowed_nodes.append(text)

    # 3) Jika whitelist menghasilkan konten, rakit HTML minimal untuk Trafilatura
    if allowed_nodes:
        mini = "<article>" + "".join(f"<p>{t}</p>" for t in allowed_nodes) + "</article>"
        return mini

    # 4) Jika tidak ada yang lolos (kasus edge), balikin dokumen setelah pembersihan ringan
    for sel in ["script", "ins", "iframe", ".ads-placeholder", "[data-ad]"]:
        for n in soup.select(sel):
            n.decompose()
    return str(soup)

def _postprocess_tribun(text: str) -> str:
    t = _norm(text)

    # Hapus byline “TRIBUNNEWS.COM, KOTA -” di awal paragraf
    t = re.sub(r'\bTRIBUNNEWS\.COM,\s*[^-]{1,60}-\s*', ' ', t, flags=re.IGNORECASE)

    # Hapus label “Baca juga: …”
    t = re.sub(r'\bBaca juga\s*:\s*[^\n]+', ' ', t, flags=re.IGNORECASE)
    t = re.sub(r'\bLihat juga\s*:\s*[^\n]+', ' ', t, flags=re.IGNORECASE)

    # Penulis/Editor/Laporan Wartawan
    t = re.sub(r'\bPenulis:\s*[^\n]+', ' ', t, flags=re.IGNORECASE)
    t = re.sub(r'\bEditor:\s*[^\n]+', ' ', t, flags=re.IGNORECASE)
    t = re.sub(r'\bLaporan Wartawan\s+[^\n]+', ' ', t, flags=re.IGNORECASE)

    # Blok related articles markdown-style (| judul | |---| ...)
    t = re.sub(r'\|\s*[^|]+\s*\|\s*(\|---\|\s*\|\s*[^|]+\s*\|)+', ' ', t)

    # Rapikan
    t = re.sub(r'\s{2,}', ' ', t).strip()
    return t

async def extract(url: str) -> ExtractResult:
    html, final_url = await fetch_html(url)

    # Pre-clean berbasis whitelist (mencegah "kosong total")
    cleaned_html = _preclean_tribun_html(html)

    # Ekstraksi
    text = trafilatura.extract(
        cleaned_html,
        include_comments=False,
        include_images=False,
        favor_recall=True,
        target_language="id",
        url=final_url,
    )

    # Fallback AMP
    if not text or len(text.strip()) < MIN_TEXT_CHARS:
        amp = find_amp_href(html, final_url)
        if amp:
            amp_html, amp_final = await fetch_html(amp)
            amp_cleaned = _preclean_tribun_html(amp_html)
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
    clean = _postprocess_tribun(clean)

    host = urlparse(final_url).netloc.lower()
    title = "judul"
    preview = clean
    return ExtractResult(text=clean, source=host, length=len(clean), title=title, preview=preview)
