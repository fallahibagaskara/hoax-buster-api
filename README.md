# Hoax Buster — REST API (IndoBERT Hoax Detector)

> API inference untuk deteksi hoaks berbasis IndoBERT, dengan dua alur input: teks langsung dan URL artikel (ekstraksi otomatis dari media Indonesia populer). Dibangun dengan FastAPI + Transformers.

## TL;DR

- /predict → input teks langsung → prediksi.
- /predict_url → input URL berita → scrape + bersihkan + prediksi.
- Ekstraksi konten robust (suffix whitelist domain, retry/backoff, anti-SSRF, AMP fallback, cache 5 menit).
- Cocok untuk frontend web “Hoax Buster” dan otomatisasi pipeline evaluasi.

---

## Arsitektur Singkat

```bash
FastAPI
 ├─ /predict         : inferensi dari teks
 ├─ /extract         : ekstraksi teks dari URL (Trafilatura + AMP fallback)
 ├─ /predict_url     : ekstraksi + inferensi
 └─ /supported_sources
Core
 ├─ extractor.py     : normalisasi URL, whitelist domain, anti-SSRF, fetch httpx, clean text
 └─ model runtime    : IndoBERT (sequence classification)
```

---

## Fitur Utama

- **Dua Mode Input**: teks mentah & URL artikel.
- **Ekstraksi Tahan Banting**: Trafilatura, fallback amphtml, pembersihan boilerplate, validasi Content-Type, batas ukuran dokumen.
- **Keamanan Praktis**: blok localhost/IP privat (anti-SSRF), suffix-whitelist domain.
- **Kinerja**: retry eksponensial, limiter per host, cache in-memory.
- **Contract Stabil**: skema respons konsisten untuk FE.

---

## Persyaratan

- Python 3.10+
- CPU/GPU opsional (PyTorch). GPU mempercepat, bukan wajib.

---

## Dependensi (pip)

```bash
fastapi
uvicorn[standard]
httpx
transformers
torch
trafilatura
beautifulsoup4
```

---

## 🛠️ Setup

1. **Clone repo**

   ```bash
   git clone https://github.com/fallahibagaskara/hoax-buster-api.git
   cd hoax-buster-api
   ```

2. **Buat environment & install dependency**

   ```bash
   python -m venv .venv
   source .venv/bin/activate # Windows: .venv\Scripts\activate
   pip install -U pip
   pip install -r requirements.txt
   ```

---

## Lingkungan (opsional) – buat .env lalu export sebelum run (atau jadikan variabel di container):

```bash
MODEL_DIR=./models/indobert-hoax-detector/final
CACHE_TTL_SECONDS=300
REQUEST_TIMEOUT=20
```

---

## Endpoint

1. **POST /predict**

   > Inferensi dari teks langsung.

   **Request**

   ```bash
   {
   "text": "Isi artikel/berita lengkap di sini..."
   }
   ```

   **Response**

   ```bash
   {
   "label": 1,
   "p_valid": 0.12,
   "p_hoax": 0.88
   }
   ```

   > label: 0 = valid, 1 = hoax.

2. **POST /extract**

   > Ekstraksi teks dari URL (tanpa prediksi).

   **Request**

   ```bash
   {
      "url": "https://www.kompas.com/..."
   }
   ```

   **Response**

   ```bash
   {
   "text": "Konten artikel yang sudah dibersihkan...",
   "source": "kompas.com",
   "length": 4821,
   "preview": "Cuplikan 300 karakter…"
   }
   ```

   **cURL**

   ```bash
   curl -s -X POST http://localhost:8000/extract \
   -H "Content-Type: application/json" \
   -d '{"url":"https://www.kompas.com/..."}'
   ```

3. **POST /predict_url**

   > Ekstraksi + inferensi dalam satu langkah.

   **Request**

   ```bash
   {
      "url": "https://news.detik.com/..."
   }
   ```

   **Response**

   ```bash
   {
   "label": 0,
   "p_valid": 0.91,
   "p_hoax": 0.09,
   "source": "detik.com",
   "extracted_chars": 6230,
   "preview": "Cuplikan 300 karakter…"
   }
   ```

   **cURL**

   ```bash
   curl -s -X POST http://localhost:8000/predict_url \
   -H "Content-Type: application/json" \
   -d '{"url":"https://news.detik.com/..."}'
   ```

4. **GET /supported_sources**

   > Daftar domain yang saat ini didukung untuk ekstraksi URL.

   **Response**

   ```bash
   {
      "domains": ["cnnindonesia.com", "detik.com", "kompas.com", "liputan6.com", "tempo.co", "tribunnews.com", "turnbackhoax.id"]
   }
   ```

---

**Skema Error**

- **400** – URL tidak valid.
- **422** – Domain belum didukung / konten artikel terlalu pendek / gagal diekstrak.
- **500** – Kegagalan jaringan/tidak terduga saat fetching/ekstraksi.

**Contoh**

```bash
{
   "detail": "Domain 'example.com' belum didukung."
}
```

---

## Konfigurasi Ekstraksi (opinionated & production-ready)

- **Whitelist domain (suffix-match)**: mendukung subdomain seperti news.detik.com → detik.com.
- **Anti-SSRF**: blok localhost, 127._, 10._, 172.16–31._, 192.168._, ::1.
- **HTTP Fetch**: httpx async, retry eksponensial, validasi Content-Type text/html, size cap 3MB, follow redirects.
- **Trafilatura**: favor_recall=True, target_language="id", fallback ke halaman AMP jika ekstraksi pendek.
- **Cleaner**: hapus boilerplate (“Baca Juga”, CTA share, editor/penulis, dsb).
- **Limiter**: semaphore per host (default 5).
- **Cache**: in-memory 5 menit untuk URL yang sama.
- **Real talk**: Jangan pakai headless browser untuk skripsi—berat dan rawan flaky. Trafilatura + AMP sudah cukup 80–90% untuk portal arus utama.

---

## Menambah Domain Dukungan

Edit SUPPORTED_DOMAINS di extractor.py:

```bash
SUPPORTED_DOMAINS = [
  "kompas.com", "cnnindonesia.com", "tempo.co", "detik.com",
  "liputan6.com", "tribunnews.com", "turnbackhoax.id",
  # tambah di sini → "voi.id", "kumparan.com", dst.
]
```

Strategi cepat:

- Tambah suffix domain.
- Uji /extract → cek length & preview.
- Jika sering gagal, tambahkan selector khusus via BeautifulSoup di extractor.

---

## Testing Cepat

- Smoke test: jalankan cURL di atas.
- Unit (opsional): mock httpx untuk deterministik, uji cleaner dan validasi anti-SSRF.

---

## Docker (opsional tapi recommended untuk deploy)

**Dockerfile**

```bash
FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# copy source & model
COPY . .
# Pastikan folder models/ ter-copy atau mount via volume

ENV MODEL_DIR=/app/models/indobert-hoax-detector/final
ENV UVICORN_HOST=0.0.0.0
ENV UVICORN_PORT=8000

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

**Build & Run**

```bash
docker build -t hoax-buster-api:latest .
docker run --rm -p 8000:8000 -e MODEL_DIR=/app/models/indobert-hoax-detector/final hoax-buster-api:latest
```

---

## Kontrak Model

- Klasifikasi biner: label 0 = valid, label 1 = hoax.
- Max sequence length inference: 384 (diset di runtime). Jika mau 512, naikkan max_length, waspadai latensi.

---

## Roadmap (pasang prioritas yang ngasih ROI)

- Per-domain selectors untuk situs “rewel”.
- Lang detector (tolak non-ID).
- Observability: logging terstruktur + metrik (latensi, success rate per domain).
- Rate limiting global (mis. Redis) untuk hardening publik.

---

## Lisensi

MIT
Copyright © Hoax Buster.
