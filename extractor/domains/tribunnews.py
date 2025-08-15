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

# ---------- title helpers ----------
def _clean_title_tribun(raw: str) -> str:
    t = _norm(raw)
    # buang suffix brand/kanal: "- Tribunnews.com", "| TribunBogor.com", "- Health Tribunnews.com"
    t = re.sub(r'\s*[\-|–]\s*(?:[A-Za-z ]+)?\s*Tribun\w+\.com\b.*$', '', t, flags=re.IGNORECASE)
    t = re.sub(r'\s*\|\s*Tribun\w+\.com\b.*$', '', t, flags=re.IGNORECASE)
    # buang "TRIBUNNEWS.COM, KOTA - " jika pernah nyangkut di <title>
    t = re.sub(r'\bTRIBUNNEWS\.COM,\s*[^-]{1,60}-\s*', ' ', t, flags=re.IGNORECASE)
    # rapikan kutip/kurung pinggir
    t = re.sub(r'^[\'"“”‘’\[\(]+\s*', '', t)
    t = re.sub(r'\s*[\'"“”‘’\]\)]+$', '', t)
    return _norm(t)

def _extract_title_candidates_tribun(soup: BeautifulSoup) -> list[str]:
    cands = []
    # 1) h1 spesifik
    h1id = soup.select_one("h1#arttitle")
    if h1id:
        cands.append(_norm(h1id.get_text(" ", strip=True)))
    # 2) h1 umum (cadangan)
    if not h1id:
        h1 = soup.find("h1")
        if h1:
            cands.append(_norm(h1.get_text(" ", strip=True)))
    # 3) meta og/twitter title
    for m in soup.select("meta[property='og:title'], meta[name='twitter:title']"):
        content = _norm(m.get("content") or "")
        if content:
            cands.append(content)
    # 4) <title>
    if soup.title and soup.title.string:
        cands.append(_norm(soup.title.string))

    # dedup (case-insensitive, pertahankan urutan)
    seen, uniq = set(), []
    for t in cands:
        k = t.lower()
        if t and k not in seen:
            seen.add(k)
            uniq.append(t)
    return uniq

def _pick_best_title_tribun(cands: list[str]) -> str | None:
    BEST_MIN_LEN = 6
    seen = set()
    cleaned = []
    for c in cands:
        ct = _clean_title_tribun(c)
        if not ct or len(ct) < BEST_MIN_LEN:
            continue
        k = ct.lower()
        if k in seen:
            continue
        # filter super generik
        if ct.lower() in ("tribunnews.com", "tribunbogor.com", "tribunstyle.com", "beranda", "news"):
            continue
        seen.add(k)
        cleaned.append(ct)
    return cleaned[0] if cleaned else None

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

    # --- tarik judul dari HTML asli (paling akurat)
    soup_title = BeautifulSoup(html, "html.parser")
    title_cands = _extract_title_candidates_tribun(soup_title)
    title = _pick_best_title_tribun(title_cands) or ""

    # lanjut proses konten seperti sebelumnya
    cleaned_html = _preclean_tribun_html(html)
    text = trafilatura.extract(
        cleaned_html,
        include_comments=False,
        include_images=False,
        favor_recall=True,
        target_language="id",
        url=final_url,
    )
    # ... (AMP fallback & error handling tetap sama)

    clean = clean_text_basic(text)
    clean = _postprocess_tribun(clean)

    host = urlparse(final_url).netloc.lower()
    return ExtractResult(
        text=clean,
        source=host,
        length=len(clean),
        title=title if title else _clean_title_tribun(clean[:120]),  # fallback aman
        content=clean,
    )
