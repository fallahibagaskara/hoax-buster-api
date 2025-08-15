import re
from urllib.parse import urlparse, urlunparse
import trafilatura
from bs4 import BeautifulSoup
from ..base import ExtractResult, MIN_TEXT_CHARS, fetch_html, clean_text_basic

# ---------- helpers ----------
def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()

def _strip_kompas_prefix(t: str) -> str:
    # hapus pembuka lokasi + "KOMPAS.com - "
    t = re.sub(r'^\s*[A-Z][A-Z\s\.\-/()]{1,40},\s*KOMPAS\.com\s*-\s+', '', t)
    t = re.sub(r'^\s*KOMPAS\.com\s*-\s+', '', t)
    return t

def _strip_kompas_credits(t: str) -> str:
    # hapus (Sumber: Kompas.com ....) baik dengan/ tanpa kurung penutup
    t = re.sub(r'\(?\s*Sumber\s*:\s*Kompas\.com[^\n)]*\)?\s*', ' ', t, flags=re.IGNORECASE)
    # hapus penulis/editor
    t = re.sub(r'\b(Penulis|Reporter|Editor)\s*:\s*[^|•\n]+(?:\s*[|•]\s*[^|•\n]+)*', ' ', t, flags=re.IGNORECASE)
    return t

def _build_show_all_url(final_url: str) -> str:
    from urllib.parse import urlparse, parse_qs, urlencode
    p = urlparse(final_url)
    q = parse_qs(p.query)
    q["page"] = ["all"]
    new_query = urlencode({k: v[0] for k, v in q.items()})
    return urlunparse((p.scheme, p.netloc, p.path, "", new_query, ""))

def _collect_read_content(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")

    # Unwrap anchor "Baca juga" agar teks diambil trafilatura/collector tanpa link
    for a in soup.select("a.inner-link-baca-juga"):
        a.replace_with(a.get_text(" ", strip=True))

    # buang noise
    selectors_to_kill = [
        ".gate-kgplus", ".read__paging", ".kompasidRec",
        ".ads-on-body", ".ads-partner-wrap", ".advertisement", ".ads", "#ads",
        "iframe", "script", "style", ".liftdown_v2_tanda",
        ".read__byline", ".read__credit", ".read__photo", ".fb-quote",
    ]
    for sel in selectors_to_kill:
        for n in soup.select(sel):
            n.decompose()

    wrapper = soup.select_one(".read__content") or soup
    chunks = []
    for node in wrapper.find_all(["p", "h2", "h3"]):
        txt = _norm(node.get_text(" ", strip=True))
        if not txt:
            continue
        if txt.lower().startswith("baca juga"):  # drop paragraf CTA baca juga
            continue
        # drop CTA donasi khas kompas di ujung
        low = txt.lower()
        if "terangi negeri dengan literasi" in low and "kompas.com" in low:
            continue
        chunks.append(txt)

    return _norm(" ".join(chunks))

# ---------- title helpers ----------
def _clean_title_kompas(raw: str) -> str:
    t = _norm(raw)
    # buang suffix brand/kanal: " - Kompas.com" / " | Kompas.com"
    t = re.sub(r'\s*([\-–|])\s*Kompas\.com\b.*$', '', t, flags=re.IGNORECASE)
    # buang prefix "KOMPAS.com - "
    t = re.sub(r'^\s*KOMPAS\.com\s*-\s+', '', t, flags=re.IGNORECASE)
    # rapikan kutip/kurung pinggir
    t = re.sub(r'^[\'"“”‘’\[\(]+\s*', '', t)
    t = re.sub(r'\s*[\'"“”‘’\]\)]+$', '', t)
    return _norm(t)

def _extract_title_candidates_kompas(soup: BeautifulSoup) -> list[str]:
    cands = []
    # 1) h1 utama
    h1 = soup.select_one("h1.read__title") or soup.find("h1")
    if h1:
        cands.append(_norm(h1.get_text(" ", strip=True)))
    # 2) meta og:title / twitter:title / <title>
    for m in soup.select("meta[property='og:title'], meta[name='twitter:title']"):
        content = _norm(m.get("content") or "")
        if content:
            cands.append(content)
    if soup.title and soup.title.string:
        cands.append(_norm(soup.title.string))

    # dedup (case-insensitive)
    seen, uniq = set(), []
    for t in cands:
        k = t.lower()
        if k not in seen and t:
            seen.add(k); uniq.append(t)
    return uniq

def _pick_best_title_kompas(cands: list[str]) -> str | None:
    BEST_MIN_LEN = 6
    seen = set()
    cleaned = []
    for c in cands:
        ct = _clean_title_kompas(c)
        if not ct or len(ct) < BEST_MIN_LEN:
            continue
        k = ct.lower()
        if k in seen:
            continue
        # filter yang terlalu generik
        if ct.lower() in ("kompas.com", "beranda", "news", "tren"):
            continue
        seen.add(k)
        cleaned.append(ct)
    return cleaned[0] if cleaned else None

# ---------- main ----------
async def extract(url: str) -> ExtractResult:
    # fetch halaman normal
    html, final_url = await fetch_html(url)

    # jika ada paging → coba ?page=all
    show_all_html = None
    try:
        if BeautifulSoup(html, "html.parser").select_one(".read__paging"):
            all_url = _build_show_all_url(final_url)
            show_all_html, final_url = await fetch_html(all_url)
    except Exception:
        show_all_html = None

    base_html = show_all_html or html

    # --- ambil judul dari base_html
    soup_for_title = BeautifulSoup(base_html, "html.parser")
    title_cands = _extract_title_candidates_kompas(soup_for_title)
    title = _pick_best_title_kompas(title_cands) or ""

    # --- ambil konten dengan parser manual (atau fallback ke trafilatura)
    manual_text = _collect_read_content(base_html)
    if manual_text and len(manual_text) >= MIN_TEXT_CHARS:
        text = manual_text
    else:
        text = trafilatura.extract(
            base_html,
            include_comments=False,
            include_images=False,
            favor_recall=True,
            target_language="id",
            url=final_url,
        ) or ""

    if not text or len(text.strip()) < MIN_TEXT_CHARS:
        raise ValueError("Konten artikel berita terlalu pendek / gagal diekstrak.")

    clean = clean_text_basic(text)
    clean = _strip_kompas_prefix(clean)
    clean = _strip_kompas_credits(clean)
    clean = re.sub(r'\bBaca juga\s*:\s*[^\n]+', ' ', clean, flags=re.IGNORECASE)
    clean = re.sub(r'\s*\|\s*', ' ', clean)
    clean = re.sub(r'\s{2,}', ' ', clean).strip()

    host = urlparse(final_url).netloc.lower()
    return ExtractResult(
        text=clean,
        source=host,
        length=len(clean),
        title=title if title else _clean_title_kompas(clean[:120]),
        content=clean,
    )
