import re
from urllib.parse import urlparse, urlunparse
import trafilatura
from bs4 import BeautifulSoup
from ..base import ExtractResult, MIN_TEXT_CHARS, fetch_html, clean_text_basic

def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()

def _strip_kompas_prefix(t: str) -> str:
    t = re.sub(r'^\s*[A-Z][A-Z\s\.\-/()]{1,40},\s*KOMPAS\.com\s*-\s+', '', t)
    t = re.sub(r'^\s*KOMPAS\.com\s*-\s+', '', t)
    return t

def _strip_kompas_credits(t: str) -> str:
    t = re.sub(
    r'\(?\s*Sumber\s*:\s*Kompas\.com[^\n)]*(?:\)|$)', 
    ' ', 
    t, 
    flags=re.IGNORECASE
    )
    t = re.sub(r'\b(Penulis|Reporter|Editor)\s*:\s*[^|•\n]+(?:\s*[|•]\s*[^|•\n]+)*', ' ', t, flags=re.IGNORECASE)
    return t

def _build_show_all_url(final_url: str) -> str:
    # ubah ke ?page=all kalau ada paging
    from urllib.parse import urlparse, parse_qs, urlencode
    p = urlparse(final_url)
    q = parse_qs(p.query)
    q["page"] = ["all"]
    new_query = urlencode({k:v[0] for k,v in q.items()})
    return urlunparse((p.scheme, p.netloc, p.path, "", new_query, ""))

def _collect_read_content(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")

    # --- Unwrap anchor inline 'inner-link-baca-juga' supaya teksnya (Kompas.com) tetap ada
    for a in soup.select("a.inner-link-baca-juga"):
        a.replace_with(a.get_text(" ", strip=True))

    # --- Buang blok noise
    selectors_to_kill = [
        ".gate-kgplus",
        ".read__paging",
        ".kompasidRec",
        ".ads-on-body", ".ads-partner-wrap", ".advertisement", ".ads", "#ads",
        "iframe", "script", "style",
        ".liftdown_v2_tanda",
        ".read__byline", ".read__credit", ".read__photo",
        ".fb-quote",
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
        # buang paragraf “Baca juga: …” yang berdiri sendiri
        if txt.lower().startswith("baca juga"):
            continue
        # buang CTA donasi khas kompas di akhir
        low = txt.lower()
        if "terangi negeri dengan literasi" in low and "kompas.com" in low:
            continue
        chunks.append(txt)

    return _norm(" ".join(chunks))

async def extract(url: str) -> ExtractResult:
    # 1) fetch halaman normal
    html, final_url = await fetch_html(url)

    # 2) jika ada paging → coba ?page=all
    show_all_html = None
    try:
        if BeautifulSoup(html, "html.parser").select_one(".read__paging"):
            all_url = _build_show_all_url(final_url)
            show_all_html, final_url = await fetch_html(all_url)
    except Exception:
        show_all_html = None  # fallback aman

    # 3) Utamakan parser manual pada (show_all_html atau html)
    base_html = show_all_html or html
    manual_text = _collect_read_content(base_html)

    text = None
    if manual_text and len(manual_text) >= MIN_TEXT_CHARS:
        text = manual_text
    else:
        # fallback ke trafilatura bila manual kurang
        text = trafilatura.extract(
            base_html,
            include_comments=False,
            include_images=False,
            favor_recall=True,
            target_language="id",
            url=final_url,
        ) or ""

    if not text or len(text.strip()) < MIN_TEXT_CHARS:
        raise ValueError("Konten artikel terlalu pendek / gagal diekstrak.")

    clean = clean_text_basic(text)
    clean = _strip_kompas_prefix(clean)
    clean = _strip_kompas_credits(clean)
    clean = re.sub(r'\bBaca juga\s*:\s*[^\n]+', ' ', clean, flags=re.IGNORECASE)
    clean = re.sub(r'\s*\|\s*', ' ', clean)
    clean = re.sub(r'\s{2,}', ' ', clean).strip()

    host = urlparse(final_url).netloc.lower()
    title = "judul"
    content = clean
    return ExtractResult(text=clean, source=host, length=len(clean), title=title, content=content)
