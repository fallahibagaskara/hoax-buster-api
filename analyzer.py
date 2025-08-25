import re
from urllib.parse import urlparse

_CATEGORY_KEYWORDS = {
    "politik":    [r"mpr\b", r"dpr\b", r"pilkada\b", r"presiden\b", r"menteri\b", r"partai\b", r"perppu\b", r"omnibus law\b"],
    "ekonomi":    [r"\bbi\b", r"\botp\b", r"inflasi\b", r"\bpdb\b", r"pajak\b", r"sukuk\b", r"bea cukai\b", r"ihsg\b", r"rupiah\b", r"utang\b"],
    "bisnis":     [r"startup\b", r"investasi\b", r"pendanaan\b", r"\bipo\b", r"akuisisi\b", r"kemitraan\b"],
    "hukum":      [r"\bmk\b", r"\bma\b", r"kejagung\b", r"\bkpk\b", r"\bpolri\b", r"pidana\b", r"vonis\b", r"tersangka\b", r"pasal\b"],
    "internasional":[r"\bas\b", r"china\b", r"tiongkok\b", r"rusia\b", r"ukraina\b", r"\bpbb\b", r"asean\b", r"\bnato\b"],
    "olahraga":   [r"liga\b", r"premier\b", r"liga 1\b", r"timnas\b", r"piala\b", r"olimpiade\b", r"\bbwf\b", r"\bfifa\b", r"motogp\b", r"formula\b"],
    "hiburan":    [r"film\b", r"drama\b", r"idol\b", r"musik\b", r"album\b", r"konser\b", r"artis\b", r"seleb\b"],
    "tekno":      [r"\bai\b", r"chip\b", r"\bgpu\b", r"aplikasi\b", r"android\b", r"\bios\b", r"siber\b", r"cloud\b"],
    "otomotif":   [r"motor\b", r"mobil\b", r"otomotif\b", r"\bbbm\b", r"\bev\b", r"\bsuv\b"],
    "kesehatan":  [r"\brs\b", r"\bbpjs\b", r"stunting\b", r"vaksin\b", r"flu\b", r"kemenkes\b", r"covid\b", r"kanker\b"],
    "pendidikan": [r"kemdikbud\b", r"\bsnp\b", r"kurikulum\b", r"kampus\b", r"guru\b", r"siswa\b", r"snpm[bp]\b"],
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
    r"\bkatanya\b", r"\bforward(an)?\b", r"\bsebar(kan)?\b", r"\bshare( lah| ya)?\b",
    r"\bternyata\b", r"\bbongkar\b", r"\bwaspada\b", r"\bskandal\b",
]

_OFFICIALS = [
    r"\bkemenkeu\b", r"\bkemenkes\b", r"\bkemenlu\b", r"\bkemendagri\b", r"\bkemdikbud\b",
    r"\bpolri\b", r"\btni\b", r"\bkpk\b", r"\bmk\b", r"\bma\b", r"\bpemkot\b", r"\bpemkab\b",
    r"\bbpjs\b", r"\bbi\b", r"\bbps\b", r"\bkpu\b", r"\bbawaslu\b",
]

_FACTCHECK_HINTS = [
    r"\bcek fakta\b", r"\bfact[- ]?check\b", r"\bklarifikasi\b", r"\bdisinformasi\b", r"\bmisinformasi\b",
    r"\bhoaks?\b", r"\bsalah konteks\b", r"\bsalah atribusi\b",
]

def _has_quotes(text: str) -> int:
    return len(re.findall(r"“[^”]{10,}”|\"[^\"\n]{10,}\"", text))

def _has_numbers(text: str) -> bool:
    return bool(re.search(r'\b\d{1,3}(?:[.,]\d{3})*(?:,\d+)?\b', text))

def _has_dates(text: str) -> bool:
    return bool(re.search(r'\b(\d{1,2}\s+(jan|feb|mar|apr|mei|jun|jul|agu|sep|okt|nov|des)[a-z]*\s+\d{4})\b', text, re.IGNORECASE))

def _allcaps_ratio(text: str) -> float:
    words = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ]{2,}", text)
    if not words: return 0.0
    caps = sum(1 for w in words if w.isupper() and len(w) >= 3)
    return caps / max(1, len(words))

def analyze_evidence(url: str, title: str, content: str):
    """
    Kembalikan dict evidence + skor 0..100 (tanpa “verdict” final).
    Skor di sini jadi “credibility_score”; nanti alasan diformat sesuai label model.
    """
    host = ""
    if url:
        try:
            host = urlparse(url).netloc.lower()
        except Exception:
            host = ""

    score = 50.0
    reasons_pos, reasons_neg = [], []

    reputable = any(h in (host or "") for h in _REPUTABLE)
    if reputable:
        score += 25; reasons_pos.append("Sumber media arus utama.")
    elif host:
        reasons_neg.append("Sumber di luar whitelist media arus utama.")
    else:
        reasons_neg.append("Input teks mentah (tanpa domain).")

    qcount = _has_quotes(content)
    if qcount >= 2:
        score += 10; reasons_pos.append("Memuat banyak kutipan narasumber (≥2).")
    elif qcount == 1:
        score += 5; reasons_pos.append("Memuat kutipan narasumber.")
    else:
        reasons_neg.append("Minim kutipan narasumber.")

    if _has_numbers(content): 
        score += 5; reasons_pos.append("Ada data/angka pendukung.")
    if _has_dates(content):
        score += 3; reasons_pos.append("Ada tanggal/waktu yang jelas.")
    if any(re.search(p, content, re.IGNORECASE) for p in _OFFICIALS):
        score += 5; reasons_pos.append("Rujuk lembaga/otoritas resmi.")

    sensational_hits = sum(1 for p in _SENSATIONAL if re.search(p, content, re.IGNORECASE))
    caps_ratio = _allcaps_ratio(title + " " + content)

    is_factcheck = any(re.search(p, title + " " + content, re.IGNORECASE) for p in _FACTCHECK_HINTS)

    if not is_factcheck:
        if sensational_hits:
            penalty = min(10 + 2 * sensational_hits, 25)
            score -= penalty
            reasons_neg.append(f"Bahasa sensasional/ajakan sebar ({sensational_hits} indikasi).")
        if caps_ratio >= 0.12:
            score -= 8
            reasons_neg.append("Proporsi HURUF BESAR berlebihan.")
    else:
        reasons_pos.append("Artikel bertema cek fakta (indikasi mitigasi sensasional).")

    if len(content) < 500:
        score -= 8; reasons_neg.append("Konten sangat pendek.")
    words = [w for w in re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9']+", title.lower()) if len(w) >= 5][:6]
    missing = [w for w in words if w not in content.lower()]
    if len(words) >= 3 and len(missing) >= max(1, len(words)//2):
        score -= 8; reasons_neg.append("Konsistensi judul–isi lemah.")

    score = max(0.0, min(100.0, score))
    return {
        "host": host,
        "score": float(score),
        "reputable": reputable,
        "qcount": int(qcount),
        "has_numbers": _has_numbers(content),
        "has_dates": _has_dates(content),
        "sensational_hits": int(sensational_hits),
        "caps_ratio": float(caps_ratio),
        "is_factcheck": bool(is_factcheck),
        "reasons_pos": reasons_pos,
        "reasons_neg": reasons_neg,
    }

def reasons_from_prediction(label: int, ev: dict):
    """
    Format alasan sesuai prediksi model.
    label 0 = valid, 1 = hoax (asumsi mapping model-mu).
    """
    reasons = []

    if label == 0:
        reasons.extend(ev.get("reasons_pos", []))
        if ev.get("sensational_hits", 0) >= 2 or ev.get("caps_ratio", 0) >= 0.12:
            reasons.append("Namun terdapat indikasi bahasa sensasional; tetap perlu kewaspadaan.")
        verdict = "valid"
    else:
        reasons.extend(ev.get("reasons_neg", []))
        if ev.get("reputable") or ev.get("qcount", 0) >= 1:
            reasons.append("Ada sebagian unsur kredibel, tetapi tidak cukup menutup indikasi masalah.")
        verdict = "hoax"

    return reasons, verdict

def compute_credibility(url: str, title: str, content: str):
    ev = analyze_evidence(url, title, content)
    score = ev["score"]
    if score >= 75: verdict = "valid"
    elif score >= 55: verdict = "meragukan"
    else: verdict = "hoax"
    reasons = ev["reasons_pos"] + ev["reasons_neg"]
    return score, reasons, verdict
