import re
from urllib.parse import urlparse
import trafilatura
from bs4 import BeautifulSoup
from ..base import ExtractResult, MIN_TEXT_CHARS, fetch_html, find_amp_href, clean_text_basic

def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()

def _clean_title_detik(raw: str) -> str:
    t = _norm(raw)
    # buang suffix brand/kanal: " - detikcom", " - detikJatim", " | detikFinance", dll
    t = re.sub(r'\s*([\-–|])\s*detik(?:com|\w+)\b.*$', '', t, flags=re.IGNORECASE)
    # rapikan kutip/kurung di pinggir
    t = re.sub(r'^[\'"“”‘’\[\(]+\s*', '', t)
    t = re.sub(r'\s*[\'"“”‘’\]\)]+$', '', t)
    return _norm(t)

def _pick_best_title_detik(cands: list[str]) -> str | None:
    seen = set()
    BEST_MIN_LEN = 6
    cleaned = []
    for c in cands:
        ct = _clean_title_detik(c)
        if not ct or len(ct) < BEST_MIN_LEN:
            continue
        key = ct.lower()
        if key in seen: 
            continue
        # filter yang generik
        if ct.lower() in ("detikcom", "detik", "home", "news"):
            continue
        seen.add(key)
        cleaned.append(ct)
    return cleaned[0] if cleaned else None

def _extract_title_candidates_detik(soup: BeautifulSoup) -> list[str]:
    cands = []
    # h1
    h1 = soup.find("h1")
    if h1:
        cands.append(_norm(h1.get_text(" ", strip=True)))
    # beberapa variasi class/selector yang sering dipakai detik
    for sel in [".detail__title", ".article__title", ".title", ".headline", "meta[property='og:title']", "meta[name='twitter:title']"]:
        if sel.startswith("meta"):
            for m in soup.select(sel):
                content = _norm(m.get("content") or "")
                if content: cands.append(content)
        else:
            for n in soup.select(sel):
                t = _norm(n.get_text(" ", strip=True))
                if t: cands.append(t)
    # dedup (case-insensitive)
    uniq, seen = [], set()
    for t in cands:
        k = t.lower()
        if k not in seen:
            seen.add(k); uniq.append(t)
    return uniq

def _strip_leading_title_detik(text: str, title_candidates: list[str]) -> str:
    # hilangkan judul yang ikut di awal body (kadang terjadi)
    t = text
    for raw in title_candidates:
        title = _norm(raw)
        if not title or len(title) < 5:
            continue
        title_re = re.escape(title)
        # toleransi brand suffix: " - detikcom|detikJatim"
        pattern = r'\A\s*' + title_re + r'(?:\s*(?:[-–|]\s*detik(?:com|\w+)\b.*)?)?\s*[:\-–,]?\s*'
        new_t, n = re.subn(pattern, '', t, flags=re.IGNORECASE)
        if n > 0:
            t = new_t
            break
    return _norm(t)

def _preclean_detik_html(html: str) -> tuple[str, list[str]]:
    soup = BeautifulSoup(html, "html.parser")

    # ambil kandidat judul SEBELUM buang-buang node
    title_candidates = _extract_title_candidates_detik(soup)

    selectors = [
        "figure", "figcaption",
        ".detail__media", ".media__caption", ".photo__caption", ".foto__caption",
        ".img__caption", ".image__caption", ".pic__caption", ".caption", ".caption__text",
        ".artikel__foto", ".media__credit", ".read__also", ".read__also--item",
        ".read__more", ".advertisement", ".ad__slot", ".parallax__caption",
    ]
    for sel in selectors:
        for node in soup.select(sel):
            node.decompose()

    for node in soup.find_all(True):
        txt = node.get_text(" ", strip=True)
        if not txt: 
            continue
        low = txt.lower()
        if (low.startswith("foto:") or " foto:" in low or " detik" in low) and len(txt) <= 220:
            node.decompose()

    for node in soup.find_all(True):
        txt = node.get_text(" ", strip=True)
        if not txt:
            continue
        low = txt.lower()
        if ("saksikan" in low or "tonton" in low) and "detik" in low:
            node.decompose()

    return str(soup), title_candidates

def _postprocess_detik(text: str, title_candidates: list[str]) -> str:
    t = text
    # artefak umum
    t = re.sub(r'\s*\b[Ff]oto[:：]\s*[^。.!?\n\r|]*?(detik(?:com)?|detik\w+)[^。.!?\n\r|]*\s*\|?', ' ', t)
    t = re.sub(r'\s*\b[Ss]aksikan\b[^:]{0,100}\bdetik\w*[^:]{0,100}:', ' ', t)
    t = re.sub(r'\s*\b[Tt]onton\b[^:]{0,100}\bdetik\w*[^:]{0,100}:', ' ', t)
    # inisial reporter/editor
    t = re.sub(r'\s*\([a-z]{2,5}/[a-z]{2,5}\)\s*', ' ', t, flags=re.IGNORECASE)
    # hapus judul yang ikut di awal
    t = _strip_leading_title_detik(_norm(t), title_candidates)
    # rapikan
    t = re.sub(r'\s*\|\s*', ' ', t)
    t = re.sub(r'\s{2,}', ' ', t).strip()
    return t

async def extract(url: str) -> ExtractResult:
    html, final_url = await fetch_html(url)
    cleaned_html, title_cands = _preclean_detik_html(html)

    text = trafilatura.extract(
        cleaned_html, include_comments=False, include_images=False,
        favor_recall=True, target_language="id", url=final_url
    )

    if not text or len(text.strip()) < MIN_TEXT_CHARS:
        amp = find_amp_href(html, final_url)
        if amp:
            amp_html, amp_final = await fetch_html(amp)
            amp_cleaned, amp_title_cands = _preclean_detik_html(amp_html)
            text2 = trafilatura.extract(
                amp_cleaned, include_comments=False, include_images=False,
                favor_recall=True, target_language="id", url=amp_final
            )
            if text2 and len(text2.strip()) > len(text or ""):
                text, final_url, title_cands = text2, amp_final, amp_title_cands

    if not text or len(text.strip()) < MIN_TEXT_CHARS:
        raise ValueError("Konten artikel berita terlalu pendek / gagal diekstrak.")

    clean = clean_text_basic(text)
    clean = _postprocess_detik(clean, title_cands)

    # tentukan judul
    title = _pick_best_title_detik(title_cands) or ""
    if not title:
        title = _clean_title_detik(clean[:120])

    host = urlparse(final_url).netloc.lower()
    return ExtractResult(
        text=clean,
        source=host,
        length=len(clean),
        title=title,
        content=clean,
    )