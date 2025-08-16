import re
from urllib.parse import urlparse

_CATEGORY_KEYWORDS = {
    "politik":    [r"mpr\b", r"dpr\b", r"pilkada\b", r"presiden\b", r"menteri\b", r"partai\b", r"perppu\b", r"omnibus law\b"],
    "ekonomi":    [r"bi\b", r"o[t]*p\b", r"inflasi\b", r"pdb\b", r"pajak\b", r"sukuk\b", r"bea cukai\b", r"ihsg\b", r"rupiah\b", r"utang\b"],
    "bisnis":     [r"startup\b", r"investasi\b", r"pendanaan\b", r"ipo\b", r"akuisisi\b", r"kemitraan\b"],
    "hukum":      [r"mk\b", r"ma\b", r"kejagung\b", r"kpk\b", r"polri\b", r"pidana\b", r"vonis\b", r"tersangka\b", r"pasal\b"],
    "internasional":[r"as\b", r"china\b", r"tiongkok\b", r"rusia\b", r"ukraina\b", r"pbb\b", r"asean\b", r"nato\b"],
    "olahraga":   [r"liga\b", r"premier\b", r"liga 1\b", r"timnas\b", r"piala\b", r"olimpiade\b", r"bwf\b", r"fifa\b", r"motogp\b", r"formula\b"],
    "hiburan":    [r"film\b", r"drama\b", r"idol\b", r"musik\b", r"album\b", r"konser\b", r"artis\b", r"seleb\b"],
    "tekno":      [r"ai\b", r"chip\b", r"gpu\b", r"aplikasi\b", r"android\b", r"ios\b", r"siber\b", r"startup tech\b", r"cloud\b"],
    "otomotif":   [r"motor\b", r"mobil\b", r"otomotif\b", r"bbm\b", r"ev\b", r"suv\b"],
    "kesehatan":  [r"rs\b", r"bpjs\b", r"stunting\b", r"vaksin\b", r"flu\b", r"kemenkes\b", r"covid\b", r"kanker\b"],
    "pendidikan": [r"kemdikbud\b", r"snp\b", r"kurikulum\b", r"kampus\b", r"guru\b", r"siswa\b", r"snpm[bp]\b"],
    "sains":      [r"riset\b", r"penelitian\b", r"ilmuwan\b", r"makalah\b", r"jurnal\b"],
    "lifestyle":  [r"fesyen\b", r"kuliner\b", r"travel\b", r"wisata\b", r"gaya hidup\b", r"relationship\b"],
}

def infer_category(title: str, content: str) -> tuple[str, float]:
    text = f"{title}. {content}".lower()
    scores = {}
    for cat, pats in _CATEGORY_KEYWORDS.items():
        hits = sum(1 for p in pats if re.search(p, text))
        if hits:
            scores[cat] = hits
    if not scores:
        return ("umum", 0.3)
    # pilih kategori dengan hit terbanyak; confidence sederhana
    cat = max(scores, key=scores.get)
    total = sum(scores.values())
    conf = min(0.95, max(0.4, scores[cat] / (total or 1)))
    return (cat, float(conf))

_REPUTABLE = {
    "kompas.com", "cnnindonesia.com", "detik.com", "liputan6.com",
    "tempo.co", "kumparan.com", "tribunnews.com", "antaranews.com",
    "cnbcindonesia.com", "beritasatu.com",
}
_SENSATIONAL = [
    r"\b100%\s*ampuh\b", r"\bfix\b", r"\bterbukti hoax\b",
    r"\bheboh\b", r"\bviral\b", r"\bmengerikan\b", r"\bkonspirasi\b",
    r"\bforward(an)?\b", r"\bkatanya\b",
]

def compute_credibility(url: str, title: str, content: str) -> tuple[float, list[str], str]:
    host = ""
    if url:
        try:
            host = urlparse(url).netloc.lower()
        except Exception:
            host = ""

    score = 50.0
    reasons: list[str] = []

    if host:
        if any(h in host for h in _REPUTABLE):
            score += 25; reasons.append("Sumber media arus utama.")
        else:
            reasons.append("Sumber di luar whitelist media arus utama.")
    else:
        reasons.append("Input teks mentah (tanpa domain).")

    has_quote = bool(re.search(r'“.+?”|".+?"', content))
    has_number = bool(re.search(r'\b\d{1,3}(?:[.,]\d{3})*(?:,\d+)?\b', content))
    if has_quote: score += 5; reasons.append(" Memuat kutipan narasumber.")
    if has_number: score += 5; reasons.append(" Memuat data/angka.")

    hits = sum(1 for p in _SENSATIONAL if re.search(p, content, re.IGNORECASE))
    if hits:
        penalty = min(10 + 2*hits, 25)
        score -= penalty
        reasons.append(f"Ditemukan {hits} indikasi bahasa sensasional.")

    if len(content) < 500:
        score -= 10; reasons.append("Konten sangat pendek.")

    words = [w for w in re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9']+", title.lower()) if len(w) >= 5][:6]
    missing = [w for w in words if w not in content.lower()]
    if len(words) >= 3 and len(missing) >= max(1, len(words)//2):
        score -= 10; reasons.append("Konsistensi judul–isi lemah.")

    score = max(0.0, min(100.0, score))
    if score >= 75: verdict = "valid"
    elif score >= 55: verdict = "meragukan"
    else: verdict = "hoax"
    return score, reasons, verdict
