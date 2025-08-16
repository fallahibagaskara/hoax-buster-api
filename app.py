from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, HttpUrl
import torch
from transformers import BertTokenizer, BertForSequenceClassification
from fastapi.middleware.cors import CORSMiddleware
from extractor import extract_article, SUPPORTED_DOMAINS
from analyzer import infer_category, compute_credibility

app = FastAPI(title="IndoBERT Hoax Detector")

app.add_middleware(
    CORSMiddleware,
    allow_origins = ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

MODEL_DIR = "./models/indobert-hoax-detector/final"
tokenizer = BertTokenizer.from_pretrained(MODEL_DIR)
model = BertForSequenceClassification.from_pretrained(MODEL_DIR).eval()

class URLIn(BaseModel):
    url: HttpUrl

class PredictOut(BaseModel):
    label: int
    p_valid: float
    p_hoax: float
    source: str
    extracted_chars: int
    title: str
    content: str
    category: str | None = None
    verdict: str | None = None        
    confidence: float | None = None   
    reasons: list[str] | None = None  
    credibility_score: float | None = None 

class Item(BaseModel):
    text: str

def _guess_title(text: str) -> str:
    t = (text or "").strip()
    if not t:
        return ""
    # ambil kalimat/line pertama, batasi panjang 120
    first = t.split("\n", 1)[0].split(". ", 1)[0]
    title = first if 10 <= len(first) <= 120 else t[:120]
    return title.strip()

@torch.inference_mode()
def _predict_single(text: str):
    inputs = tokenizer([text], truncation=True, padding=True, max_length=384, return_tensors="pt")
    logits = model(**inputs).logits
    probs = torch.softmax(logits, dim=-1)[0].tolist()
    pred = int(torch.argmax(logits, dim=-1).item())
    return {"label": pred, "p_valid": probs[0], "p_hoax": probs[1]}

@app.post("/predict", response_model=PredictOut)
@torch.inference_mode()
def predict(item: Item):
    pred = _predict_single(item.text)

    title = _guess_title(item.text)
    cat, cat_conf = infer_category(title, item.text)
    score, reasons, verdict = compute_credibility(None, title, item.text)  # url=None

    return PredictOut(
        label=pred["label"],
        p_valid=float(pred["p_valid"]),
        p_hoax=float(pred["p_hoax"]),
        source="(raw-text)",
        extracted_chars=len(item.text or ""),
        title=title,
        content=item.text,
        category=cat,
        verdict=verdict,
        confidence=float(cat_conf),
        reasons=reasons,
        credibility_score=float(score),
    )

@app.get("/supported_sources")
def supported_sources():
    uniq = sorted(set(SUPPORTED_DOMAINS))
    display = sorted(set([d.replace("www.","") for d in uniq]))
    return {"domains": display}

@app.post("/extract")
async def extract_url(payload: URLIn):
    try:
        ext = await extract_article(str(payload.url))
        return {"text": ext.text, "source": ext.source, "length": ext.length, "title": ext.title, "content": ext.content}
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Gagal mengambil/ekstrak artikel berita: {e}")

@app.post("/predict_url", response_model=PredictOut)
async def predict_url(payload: URLIn):
    try:
        ext = await extract_article(str(payload.url))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Gagal mengambil/ekstrak artikel berita: {e}")

    cat, cat_conf = infer_category(ext.title, ext.content)
    score, reasons, verdict = compute_credibility(str(payload.url), ext.title, ext.content)
    ext.category = cat
    ext.confidence = float(cat_conf)
    ext.credibility_score = float(score)
    ext.verdict = verdict
    ext.reasons = reasons

    pred = _predict_single(ext.text)
    return PredictOut(
        label=pred["label"],
        p_valid=float(pred["p_valid"]),
        p_hoax=float(pred["p_hoax"]),
        source=ext.source.replace("www.",""),
        extracted_chars=ext.length,
        title=ext.title,
        content=ext.content,
        category=ext.category,
        verdict=ext.verdict,
        confidence=ext.confidence,
        reasons=ext.reasons,
        credibility_score=ext.credibility_score,
    )
