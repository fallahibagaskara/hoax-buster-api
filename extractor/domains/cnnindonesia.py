import re
from urllib.parse import urlparse
import trafilatura
from bs4 import BeautifulSoup
from ..base import ExtractResult, MIN_TEXT_CHARS, fetch_html, find_amp_href, clean_text_basic

# -------- Helpers --------
def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()

def _clean_title(raw: str) -> str:
    t = _norm(raw)

    # buang prefix breaking/newsy
    t = re.sub(r'^\s*(breaking\s+news\s*:\s*|breaking\s+news\s+cnn\s+indonesia\s*[:-]?\s*)',
               '', t, flags=re.IGNORECASE)

    # buang suffix brand & kanal
    t = re.sub(r'\s*[-|–]\s*cnn indonesia\b.*$', '', t, flags=re.IGNORECASE)

    # buang sisa “| CNN Indonesia” atau “- CNN Indonesia”
    t = re.sub(r'\s*(\||-)\s*cnn indonesia\b.*$', '', t, flags=re.IGNORECASE)

    # rapiin kutip yang dobel/longgar
    t = re.sub(r'^[\'"“”‘’\[\(]+\s*', '', t)
    t = re.sub(r'\s*[\'"“”‘’\]\)]+$', '', t)

    return _norm(t)

def _pick_best_title(cands: list[str]) -> str | None:
    """
    Prioritas: h1 dulu (karena kamu push h1 paling awal), lalu og:title/twitter:title.
    Saring kandidat yang terlalu pendek/generic, bersihin brand suffix.
    """
    seen = set()
    BEST_MIN_LEN = 8

    cleaned = []
    for c in cands:
        ct = _clean_title(c)
        key = ct.lower()
        if not ct or len(ct) < BEST_MIN_LEN:
            continue
        if key in seen:
            continue
        # filter judul generik
        if ct.lower() in ("cnn indonesia", "beranda", "news"):
            continue
        seen.add(key)
        cleaned.append(ct)

    return cleaned[0] if cleaned else None


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

    # hapus blok non-body
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

    for node in soup.find_all(True):
        txt = node.get_text(" ", strip=True)
        if not txt:
            continue
        low = txt.lower()
        if low.startswith("breaking news cnn indonesia"):
            node.decompose(); continue
        if ("[gambas:video cnn" in low) or ("[gambar:video cnn" in low) \
           or ("[gambas:gambar cnn" in low) or ("[gambar:gambar cnn" in low):
            node.decompose(); continue
        if low.startswith("lihat juga"):
            node.decompose(); continue

    # kandidat judul sebelum render
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

def _strip_cnn_dateline(t: str) -> str:
    # Pola: "Jumat, 15 Agu 2025 11:36 WIB" (opsional diawali "CNN Indonesia")
    t = re.sub(
        r'^\s*(?:cnn\s*indonesia\s*)?(?:[,•-]?\s*)?'
        r'(?:senin|selasa|rabu|kamis|jumat|sabtu|minggu)\s*,?\s*'
        r'\d{1,2}\s+\w+\s+\d{4}\s+\d{1,2}:\d{2}\s*wib\s*',
        ' ',
        t, flags=re.IGNORECASE
    )

    # Pola umum dateline: "Jakarta, CNN Indonesia -- " / "—" / "-" (tanpa tanggal)
    t = re.sub(
        r'^\s*[A-Za-zÀ-ÿ .\'-]+,\s*cnn\s*indonesia\s*[—–-]{1,2}\s*',
        ' ',
        t, flags=re.IGNORECASE
    )

    # Kalau ada format: "... WIB Jakarta, CNN Indonesia -- " (tanggal + lokasi)
    t = re.sub(
        r'^\s*(?:.*?wib\s+)?[A-Za-zÀ-ÿ .\'-]+,\s*cnn\s*indonesia\s*[—–-]{1,2}\s*',
        ' ',
        t, flags=re.IGNORECASE
    )

    # Back-up: "CNN Indonesia --" di paling awal tanpa lokasi
    t = re.sub(
        r'^\s*cnn\s*indonesia\s*[—–-]{1,2}\s*',
        ' ',
        t, flags=re.IGNORECASE
    )
    return t

def _postprocess_cnn(text: str, title_candidates: list[str]) -> str:
    t = text

    # hapus dateline header lebih dulu
    t = _strip_cnn_dateline(_norm(t))

    # buang artefak multimedia & CTA
    t = re.sub(r'\[Gambar:Gambar CNN\]', ' ', t, flags=re.IGNORECASE)
    t = re.sub(r'\[Gambas:Gambar CNN\]', ' ', t, flags=re.IGNORECASE)
    t = re.sub(r'\[Gambar:Video CNN\]', ' ', t, flags=re.IGNORECASE)
    t = re.sub(r'\[Gambas:Video CNN\]', ' ', t, flags=re.IGNORECASE)
    t = re.sub(r'^\s*BREAKING NEWS CNN Indonesia[^\n]*', ' ', t, flags=re.IGNORECASE)
    t = re.sub(r'\bLihat Juga\s*:\s*[^\n]+', ' ', t, flags=re.IGNORECASE)

    # inisial reporter/editor di mana saja, contoh: (mnf/ugo)
    t = re.sub(r'\s*\([a-z]{2,5}/[a-z]{2,5}\)\s*', ' ', t, flags=re.IGNORECASE)

    # strip judul di awal
    t = _strip_leading_title(_norm(t), title_candidates)

    # potong footer "TOPIK TERKAIT / ARTIKEL TERKAIT / TERKAIT LAINNYA DI DETIKNETWORK"
    t = re.sub(
        r'\b(TOPIK\s+TERKAIT|ARTIKEL\s+TERKAIT|TERKAIT\s+LAINNYA\s+DI\s+DETIKNETWORK)\b.*$',
        ' ',
        t, flags=re.IGNORECASE | re.DOTALL
    )

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
        raise ValueError("Konten artikel berita terlalu pendek / gagal diekstrak.")

    clean = clean_text_basic(text)
    clean = _postprocess_cnn(clean, title_cands)

    # ---- NEW: tentukan judul ----
    title = _pick_best_title(title_cands) or ""
    # fallback terakhir: pakai 120 huruf pertama (jarang diperlukan)
    if not title:
        title = _clean_title(clean[:120])

    host = urlparse(final_url).netloc.lower()
    return ExtractResult(
        text=clean,
        source=host,
        length=len(clean),
        title=title,
        content=clean,
    )
