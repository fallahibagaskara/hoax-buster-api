# Hoax Buster API

A production-ready REST API for Indonesian news hoax detection powered by IndoBERT transformer model. The system supports both direct text input and automatic extraction from Indonesian news sources.

## Overview

This API provides real-time hoax detection capabilities for Indonesian news articles through two main workflows:

- **Direct Text Analysis**: Submit text directly for immediate classification
- **URL Extraction**: Automatically extract and analyze content from supported Indonesian news websites

The underlying model is fine-tuned IndoBERT (Indonesian BERT) trained on a curated dataset of authentic and hoax news articles from major Indonesian media outlets.

## Key Features

- **Dual Input Modes**: Process raw text or extract from URLs
- **Robust Content Extraction**: Trafilatura-based extraction with AMP fallback
- **Security Hardened**: SSRF protection, domain whitelisting, content-type validation
- **Performance Optimized**: In-memory caching, exponential backoff retry, connection pooling
- **Production Ready**: Comprehensive error handling, structured logging, health checks

## Technical Stack

- **Framework**: FastAPI 0.116+
- **ML Model**: IndoBERT (indobenchmark/indobert-base-p1)
- **Deep Learning**: PyTorch + Transformers
- **Content Extraction**: Trafilatura, BeautifulSoup4
- **Database**: MongoDB (optional, for result persistence)
- **HTTP Client**: httpx (async)

## Requirements

- Python 3.10 or higher
- 4GB RAM minimum (8GB recommended for production)
- CPU or GPU (GPU accelerates inference but not required)

## Installation

### 1. Clone Repository

```bash
git clone https://github.com/fallahibagaskara/hoax-buster-api.git
cd hoax-buster-api
```

### 2. Create Virtual Environment

```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

### 3. Install Dependencies

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### 4. Environment Configuration (Optional)

Create `.env` file:

```env
PORT=8000

MODEL_DIR=./models
CACHE_TTL_SECONDS=300
REQUEST_TIMEOUT=20

MONGODB_URI=mongodb://localhost:27017
MONGO_DB=hoaxbuster
MONGO_COLL=articles
```

## Quick Start

### Start Server

```bash
uvicorn app:app --host 0.0.0.0 --port 8000 --proxy-headers
```

Server will be available at `http://localhost:8000`

## API Documentation

### Core Endpoints

#### 1. Health Check

Check API and model status.

**Endpoint**: `GET /health`

**Response**:

```json
{
  "status": "healthy",
  "model_loaded": true,
  "tokenizer_loaded": true
}
```

---

#### 2. Model Information

Retrieve model architecture and training metrics.

**Endpoint**: `GET /model_info`

**Response**:

```json
{
  "model_type": "IndoBERT",
  "base_model": "indobenchmark/indobert-base-p1",
  "num_labels": 2,
  "labels": ["VALID", "HOAX"],
  "training_history": {
    "best_val_f1": 0.9729,
    "best_val_acc": 0.9576,
    "epochs_trained": 5
  },
  "learning_rate": 2e-5
}
```

---

#### 3. Text Prediction

Analyze raw text for hoax indicators.

**Endpoint**: `POST /predict`

**Request Body**:

```json
{
  "text": "Your news article content here..."
}
```

**Response**:

```json
{
  "label": 0,
  "p_valid": 0.9872,
  "p_hoax": 0.0128,
  "source": "(raw-text)",
  "extracted_chars": 1234,
  "total_sentences": 15,
  "title": "Article title extracted from content",
  "content": "Full article content",
  "category": "politik",
  "verdict": "valid",
  "confidence": 0.9872,
  "reasons": [
    "Input teks mentah (tanpa domain).",
    "Memuat kutipan narasumber.",
    "Ada data/angka pendukung."
  ],
  "credibility_score": 87.5,
  "published_at": null,
  "inference_ms": 45.2,
  "total_ms": 52.1,
  "extraction_ms": 0.0
}
```

**cURL Example**:

```bash
curl -X POST http://localhost:8000/predict \
  -H "Content-Type: application/json" \
  -d '{"text": "Presiden mengumumkan kebijakan ekonomi baru..."}'
```

---

#### 4. URL Prediction

Extract and analyze content from news URL.

**Endpoint**: `POST /predict_url`

**Request Body**:

```json
{
  "url": "https://www.kompas.com/article/..."
}
```

**Response**:

```json
{
  "label": 0,
  "p_valid": 0.91,
  "p_hoax": 0.09,
  "source": "kompas.com",
  "extracted_chars": 6230,
  "total_sentences": 42,
  "title": "Extracted article title",
  "content": "Full extracted article content",
  "category": "ekonomi",
  "verdict": "valid",
  "confidence": 0.91,
  "reasons": [
    "Sumber media arus utama.",
    "Memuat banyak kutipan narasumber (≥2).",
    "Ada data/angka pendukung."
  ],
  "credibility_score": 92.0,
  "published_at": "2024-01-15T10:30:00Z",
  "inference_ms": 77.5,
  "total_ms": 429.4,
  "extraction_ms": 349.1
}
```

**cURL Example**:

```bash
curl -X POST http://localhost:8000/predict_url \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.kompas.com/read/..."}'
```

---

#### 5. Content Extraction

Extract clean content from URL without prediction.

**Endpoint**: `POST /extract`

**Request Body**:

```json
{
  "url": "https://news.detik.com/article/..."
}
```

**Response**:

```json
{
  "text": "Konten artikel berita yang sudah dibersihkan...",
  "source": "kompas.com",
  "length": 4821,
  "title": "Judul artikel berita",
  "content": "Konten artikel berita"
}
```

---

#### 6. Supported Sources

List all supported news domains for URL extraction.

**Endpoint**: `GET /supported_sources`

**Response**:

```json
{
  "domains": [
    "antaranews.com",
    "cnnindonesia.com",
    "detik.com",
    "kompas.com",
    "kumparan.com",
    "liputan6.com",
    "tempo.co",
    "tribunnews.com"
  ]
}
```

---

#### 7. List Hoax Articles

Retrieve paginated list of detected hoax articles from database.

**Endpoint**: `GET /articles/hoax`

**Query Parameters**:

- `page` (int): Page number (default: 1)
- `limit` (int): Items per page (default: 10, max: 100)

**Response**:

```json
{
  "page": 1,
  "limit": 10,
  "total": 145,
  "total_pages": 15,
  "has_next": true,
  "has_prev": false,
  "items": [
    {
      "id": "507f1f77bcf86cd799439011",
      "url": "https://example.com/article",
      "source": "example.com",
      "title": "Article title",
      "label": 1,
      "p_hoax": 0.95,
      "verdict": "hoax",
      "published_at": "2024-01-15T10:30:00Z",
      "created_at": "2024-01-15T11:00:00Z"
    }
  ]
}
```

**cURL Example**:

```bash
curl "http://localhost:8000/articles/hoax?page=1&limit=20"
```

---

### Response Schema

#### Prediction Output

| Field               | Type   | Description                                |
| ------------------- | ------ | ------------------------------------------ |
| `label`             | int    | Classification result (0=valid, 1=hoax)    |
| `p_valid`           | float  | Probability of being valid (0-1)           |
| `p_hoax`            | float  | Probability of being hoax (0-1)            |
| `source`            | string | Content source domain                      |
| `title`             | string | Article title                              |
| `content`           | string | Full article text                          |
| `category`          | string | Article category (politik, ekonomi, etc.)  |
| `verdict`           | string | Human-readable verdict ("valid" or "hoax") |
| `confidence`        | float  | Prediction confidence score                |
| `reasons`           | array  | Explanation for the prediction             |
| `credibility_score` | float  | Overall credibility rating (0-100)         |
| `published_at`      | string | Publication timestamp (ISO 8601)           |
| `inference_ms`      | float  | Model inference time in milliseconds       |
| `total_ms`          | float  | Total request processing time              |
| `extraction_ms`     | float  | URL extraction time (if applicable)        |

#### Error Responses

**400 Bad Request**: Invalid input format

```json
{
  "detail": "Format URL tidak valid"
}
```

**422 Unprocessable Entity**: Unsupported domain or extraction failure

```json
{
  "detail": "Domain ‘example.com’ tidak didukung"
}
```

**500 Internal Server Error**: Unexpected server error

```json
{
  "detail": "Gagal mengambil artikel: Connection timeout"
}
```

---

## Content Extraction Details

### Supported Domains

Currently supports 8 major Indonesian news sources:

- antaranews.com
- cnnindonesia.com
- detik.com
- kompas.com
- kumparan.com
- liputan6.com
- tempo.co
- tribunnews.com

### Extraction Pipeline

1. **URL Validation**: Check domain whitelist and format
2. **SSRF Protection**: Block private IP ranges and localhost
3. **HTTP Fetch**: Async request with retry logic and timeout
4. **Content Extraction**: Trafilatura with Indonesian language settings
5. **AMP Fallback**: Retry with AMP version if initial extraction fails
6. **Content Cleaning**: Remove boilerplate, ads, and navigation elements
7. **Validation**: Ensure minimum content length and quality

### Security Features

- **Domain Whitelisting**: Suffix-based matching supports subdomains
- **Anti-SSRF**: Blocks localhost, 127.x.x.x, 10.x.x.x, 172.16-31.x.x, 192.168.x.x, ::1
- **Content-Type Validation**: Only process text/html responses
- **Size Limits**: Maximum 3MB response body
- **Rate Limiting**: Per-host connection semaphore (default: 5 concurrent)

### Performance Optimization

- **In-Memory Cache**: 5-minute TTL for repeated URLs
- **Connection Pooling**: Reuse HTTP connections
- **Exponential Backoff**: Automatic retry with increasing delays
- **Async Processing**: Non-blocking I/O for all network operations

---

## Model Details

### Architecture

- **Base Model**: IndoBERT (indobenchmark/indobert-base-p1)
- **Task**: Binary sequence classification
- **Input**: Preprocessed Indonesian text (max 384 tokens)
- **Output**: 2-class logits (valid/hoax)

### Preprocessing Pipeline

Text undergoes these transformations before inference:

1. **Data Cleaning**: Remove URLs, special characters, boilerplate
2. **Case Folding**: Convert to lowercase
3. **Normalization**: Standardize whitespace
4. **Stemming**: Reduce words to root form (Sastrawi)
5. **Stopword Removal**: Filter common Indonesian stopwords
6. **Tokenization**: WordPiece tokenization (IndoBERT tokenizer)

### Training Metrics

- **Best Validation F1**: 0.9729
- **Best Validation Accuracy**: 0.9576
- **Training Epochs**: 5
- **Learning Rate**: 2e-05
- **Batch Size**: 32

---

## Project Structure

```
hoax-buster-api/
├── app.py              # FastAPI application entry point
├── routes.py           # API endpoint handlers
├── model.py            # Model loading and inference
├── helpers.py          # Utility functions
├── extractor.py        # Content extraction logic
├── analyzer.py         # Evidence analysis and categorization
├── db.py               # MongoDB connection and operations
├── requirements.txt    # Python dependencies
├── models/             # Model checkpoints
│   └── indobert_final_clean_with_info.pt
├── Dockerfile          # Container build instructions
└── README.md           # Documentation
```

---

## License

MIT License - Copyright (c) 2024 Hoax Buster

---
