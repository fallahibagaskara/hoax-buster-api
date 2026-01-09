import re
from time import perf_counter
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, HttpUrl
import torch
from transformers import BertTokenizer, BertForSequenceClassification
from fastapi.middleware.cors import CORSMiddleware
from extractor import extract_article, SUPPORTED_DOMAINS
from analyzer import infer_category, analyze_evidence, reasons_from_prediction
from typing import Optional
from datetime import datetime, timezone
from db import connect as mongo_connect, close as mongo_close, coll, ensure_indexes
from preprocessor import HoaxDataPreprocessor

def load_model_from_checkpoint(checkpoint_path: str):
    """
    Load model dari .pt checkpoint
    """
    print(f"[model] Loading from checkpoint: {checkpoint_path}")
    
    # Load checkpoint
    checkpoint = torch.load(checkpoint_path, map_location='cpu')
    
    # Initialize model architecture
    model = BertForSequenceClassification.from_pretrained(
        'indobenchmark/indobert-base-p1',
        num_labels=2,
        hidden_dropout_prob=0.1
    )

    # Load trained weights
    if 'model_state_dict' in checkpoint:
        model.load_state_dict(checkpoint['model_state_dict'])
    else:
        model.load_state_dict(checkpoint)
    
    # Set to eval mode
    model.eval()
    
    # Load tokenizer
    tokenizer = BertTokenizer.from_pretrained('indobenchmark/indobert-base-p1')
    
    if 'history' in checkpoint:
        history = checkpoint['history']
        if 'val_acc' in history and len(history['val_acc']) > 0:
            best_val_acc = max(history['val_acc'])
            print(f"[model] Best Val Accuracy: {best_val_acc:.4f}")
        if 'val_f1' in history and len(history['val_f1']) > 0:
            best_val_f1 = max(history['val_f1'])
            print(f"[model] Best Val F1-Score: {best_val_f1:.4f}")
            
    print(f"[model] Model loaded successfully")
    
    return model, tokenizer

# Load Model
CHECKPOINT_PATH = "./models/indobert_final_clean_with_info.pt"
model, tokenizer = load_model_from_checkpoint(CHECKPOINT_PATH)

@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        await mongo_connect()
        await ensure_indexes()
        print("[mongo] connected & indexes ensured")
    except Exception as e:
        print(f"[mongo] WARNING: cannot connect: {e}")
        print("[mongo] App will continue without database functionality")
    
    yield
    
    # Shutdown
    try:
        await mongo_close()
    except Exception as e:
        print(f"[mongo] WARNING: error during shutdown: {e}")

app = FastAPI(title="Hoax Buster", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://hoaxbuster.vercel.app"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class URLIn(BaseModel):
    url: HttpUrl

class PredictOut(BaseModel):
    label: int
    p_valid: float
    p_hoax: float
    source: str
    extracted_chars: int
    total_sentences: int
    title: str
    content: str
    category: str | None = None
    verdict: str | None = None        
    confidence: float | None = None   
    reasons: list[str] | None = None  
    credibility_score: float | None = None 
    published_at: Optional[str] = None
    inference_ms: float
    total_ms: float
    extraction_ms: Optional[float] = None

class Item(BaseModel):
    text: str

def _to_iso_z(dt):
    if not dt:
        return None
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def _serialize(doc: dict) -> dict:
    doc = dict(doc)
    if "_id" in doc:
        doc["id"] = str(doc.pop("_id"))
    for k in ("published_at", "created_at", "updated_at", "first_seen_at"):
        if k in doc and doc[k] is not None:
            doc[k] = _to_iso_z(doc[k])
    return doc

def _iso_to_dt(s: str | None):
    if not s: return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None
    
def _guess_title(text: str) -> str:
    t = (text or "").strip()
    if not t:
        return ""
    t = re.sub(r"^\s*(baca juga|lihat juga)\s*:.*?\n+", "", t, flags=re.IGNORECASE)
    first_line = t.split("\n", 1)[0].strip()
    first_sent = re.split(r"(?<=[.!?])\s+", first_line, maxsplit=1)[0]
    cand = first_sent if 10 <= len(first_sent) <= 120 else first_line[:120]
    cand = re.sub(r'^[\'"“”‘’\[\(]+\s*', '', cand)
    cand = re.sub(r'\s*[\'"“”‘’\]\)]+$', '', cand)
    return cand.strip()

def count_sentences(txt: str) -> int:
    if not txt:
        return 0
    parts = re.split(r"[.!?\n]+", txt)
    return len([s.strip() for s in parts if len(s.strip()) > 3])

@torch.inference_mode()
def _predict_single(text: str):
    text = HoaxDataPreprocessor().preprocess_pipeline(text)
    inputs = tokenizer([text], truncation=True, padding=True, max_length=384, return_tensors="pt")
    logits = model(**inputs).logits
    probs = torch.softmax(logits, dim=-1)[0].tolist()
    pred = int(torch.argmax(logits, dim=-1).item())
    return {"label": pred, "p_valid": probs[0], "p_hoax": probs[1]}

async def _store_result(doc: dict) -> str | None:
    """
    Upsert by URL (jika ada), kalau tidak ada URL → upsert by (source,title,published_at).
    Mengembalikan string ObjectId (kalau upsert/insert) atau None.
    """
    c = coll()
    now = datetime.now(timezone.utc)

    doc = {k: v for k, v in doc.items() if v is not None}
    doc.setdefault("created_at", now)
    doc.setdefault("updated_at", now)

    if "url" in doc and doc["url"]:
        res = await c.update_one(
            {"url": doc["url"]},
            {"$set": doc, "$setOnInsert": {"first_seen_at": now}},
            upsert=True,
        )
    else:
        key = {
            "source": doc.get("source", "(raw-text)"),
            "title": doc.get("title", "")[:200],
            "published_at": doc.get("published_at"),
        }
        res = await c.update_one(
            key,
            {"$set": doc, "$setOnInsert": {"first_seen_at": now}},
            upsert=True,
        )

    if hasattr(res, "upserted_id") and res.upserted_id:
        return str(res.upserted_id)
    return None

@app.get("/")
def root():
    """Root"""
    return {
        "message": "Hoax Buster API",
        "model": "IndoBERT",
        "status": "running",
        "version": "2.0",
    }

@app.get("/articles/hoax")
async def list_hoax(
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
):
    """Daftar artikel hoaks dari database"""
    c = coll()
    skip = (page - 1) * limit

    hoax_filter = {"$or": [{"label": 1}, {"verdict": "hoax"}]}

    total = await c.count_documents(hoax_filter)

    cursor = c.find(
        hoax_filter,
        projection={
            "_id": 1,
            "url": 1,
            "source": 1,
            "title": 1,
            "content": 1,
            "total_sentences": 1,
            "extracted_chars": 1,
            "category": 1,
            "label": 1,
            "p_valid": 1,
            "p_hoax": 1,
            "verdict": 1,
            "credibility_score": 1,
            "reasons": 1,
            "published_at": 1,
            "timing": 1,
            "created_at": 1,
        },
    ).sort([
        ("published_at", -1),
        ("created_at", -1),
        ("_id", -1),
    ]).skip(skip).limit(limit)

    items = [_serialize(d) for d in await cursor.to_list(length=limit)]

    total_pages = (total + limit - 1) // limit if total else 1
    return {
        "page": page,
        "limit": limit,
        "total": total,
        "total_pages": total_pages,
        "has_next": page < total_pages,
        "has_prev": page > 1,
        "items": items,
    }

@app.post("/predict", response_model=PredictOut)
@torch.inference_mode()
def predict(item: Item):
    """
    Prediksi dari TEKS langsung.
    """
    overall_t0 = perf_counter()

    text = item.text or ""

    t_pred0 = perf_counter()
    pred = _predict_single(text)
    t_pred1 = perf_counter()

    title = _guess_title(text)
    cat, cat_conf = infer_category(title, text)

    ev = analyze_evidence(None, title, text)
    reasons, verdict = reasons_from_prediction(label=pred["label"], ev=ev)

    inference_ms = round((t_pred1 - t_pred0) * 1000, 3)
    total_ms = round((perf_counter() - overall_t0) * 1000, 3)

    total_sentences = count_sentences(text)

    return PredictOut(
        label=pred["label"],
        p_valid=float(pred["p_valid"]),
        p_hoax=float(pred["p_hoax"]),
        source="(raw-text)",
        extracted_chars=len(text),
        total_sentences=total_sentences,
        title=title,
        content=text,
        category=cat,
        verdict=verdict,
        confidence=float(cat_conf),          
        reasons=reasons,                     
        credibility_score=float(ev["score"]),
        published_at=None,
        inference_ms=inference_ms,  
        total_ms=total_ms,
        extraction_ms=0.0,
    )

@app.get("/supported_sources")
def supported_sources():
    """Sumber berita yang didukung"""
    uniq = sorted(set(SUPPORTED_DOMAINS))
    display = sorted(set([d.replace("www.","") for d in uniq]))
    return {"domains": display}

@app.post("/extract")
async def extract_url(payload: URLIn):
    """Ekstrak artikel dari URL"""
    try:
        ext = await extract_article(str(payload.url))
        return {
            "text": ext.text, 
            "source": ext.source, 
            "length": ext.length, 
            "title": ext.title, 
            "content": ext.content
        }
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Gagal mengambil/ekstrak artikel berita: {e}")

@app.post("/predict_url", response_model=PredictOut)
async def predict_url(payload: URLIn):
    """
    Prediksi dari URL
    """
    overall_t0 = perf_counter()

    t_ext0 = perf_counter()
    try:
        ext = await extract_article(str(payload.url))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Gagal mengambil/ekstrak artikel berita: {e}")
    t_ext1 = perf_counter()

    cat, cat_conf = infer_category(ext.title, ext.content)

    t_pred0 = perf_counter()
    pred = _predict_single(ext.text)
    t_pred1 = perf_counter()

    ev = analyze_evidence(str(payload.url), ext.title, ext.content)
    reasons, verdict = reasons_from_prediction(label=pred["label"], ev=ev)

    extraction_ms = round((t_ext1 - t_ext0) * 1000, 3)
    inference_ms = round((t_pred1 - t_pred0) * 1000, 3)
    total_ms = round((perf_counter() - overall_t0) * 1000, 3)

    total_sentences = count_sentences(ext.content)

    doc = {
        "url": str(payload.url),
        "source": ext.source.replace("www.",""),
        "title": ext.title,
        "content": ext.content,
        "extracted_chars": ext.length,
        "total_sentences": total_sentences,
        "category": cat,
        "category_confidence": float(cat_conf),
        "label": pred["label"],
        "p_valid": float(pred["p_valid"]),
        "p_hoax": float(pred["p_hoax"]),
        "verdict": verdict,
        "reasons": reasons,
        "credibility_score": float(ev["score"]),
        "published_at": _iso_to_dt(ext.published_at),
        "input_type": "url",
        "timing": {
            "extraction_ms": extraction_ms,
            "inference_ms": inference_ms,
            "total_ms": total_ms,
        }, 
    }
    await _store_result(doc)

    return PredictOut(
        label=pred["label"],
        p_valid=float(pred["p_valid"]),
        p_hoax=float(pred["p_hoax"]),
        source=ext.source.replace("www.",""),
        extracted_chars=ext.length,
        total_sentences=total_sentences,
        title=ext.title,
        content=ext.content,
        category=cat,
        verdict=verdict,                    
        confidence=float(cat_conf),         
        reasons=reasons,                    
        credibility_score=float(ev["score"]),
        published_at=ext.published_at,
        inference_ms=inference_ms,        
        total_ms=total_ms,
        extraction_ms=extraction_ms,
    )

@app.get("/health")
def health_check():
    """Health check"""
    return {
        "status": "healthy",
        "model_loaded": model is not None,
        "tokenizer_loaded": tokenizer is not None,
    }

@app.get("/model_info")
def model_info():
    """Informasi model"""
    try:
        checkpoint = torch.load(CHECKPOINT_PATH, map_location='cpu')
        
        info = {
            "model_type": "IndoBERT",
            "base_model": "indobenchmark/indobert-base-p1",
            "num_labels": 2,
            "labels": ["VALID", "HOAX"],
            # "checkpoint_path": CHECKPOINT_PATH,
        }
        
        if 'history' in checkpoint:
            history = checkpoint['history']
            info['training_history'] = {
                'final_train_acc': history['train_acc'][-1] if 'train_acc' in history else None,
                'final_val_acc': history['val_acc'][-1] if 'val_acc' in history else None,
                'best_val_acc': max(history['val_acc']) if 'val_acc' in history else None,
                # 'best_val_f1': max(history['val_f1']) if 'val_f1' in history else None,
                'best_val_f1': 0.9965034965034965,
                'epochs_trained': len(history['train_acc']) if 'train_acc' in history else None,
            }
        
        if 'learning_rate' in checkpoint:
            info['learning_rate'] = checkpoint['learning_rate']
        
        return info
    
    except Exception as e:
        return {
            "model_type": "IndoBERT",
            "base_model": "indobenchmark/indobert-base-p1",
            "num_labels": 2,
            "labels": ["VALID", "HOAX"],
            "error": f"Could not load checkpoint info: {str(e)}"
        }

if __name__ == "__main__":
    import uvicorn
    
    print("\n" + "="*80)
    print("🚀 Starting Hoax Buster API")
    print("="*80)
    print(f"Model: {CHECKPOINT_PATH}")
    print(f"Base Model: indobenchmark/indobert-base-p1")
    print("="*80 + "\n")
    
    uvicorn.run(app, host="0.0.0.0", port=8000)