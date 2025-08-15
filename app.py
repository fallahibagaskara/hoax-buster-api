# main.py
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, HttpUrl
import torch
from transformers import BertTokenizer, BertForSequenceClassification
from fastapi.middleware.cors import CORSMiddleware

# === IMPORT extractor baru ===
from extractor import extract_article, SUPPORTED_DOMAINS

app = FastAPI(title="IndoBERT Hoax Detector")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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
    preview: str

class Item(BaseModel):
    text: str

@torch.inference_mode()
def _predict_single(text: str):
    # aman untuk dipakai dari endpoint manapun
    inputs = tokenizer([text], truncation=True, padding=True, max_length=384, return_tensors="pt")
    logits = model(**inputs).logits
    probs = torch.softmax(logits, dim=-1)[0].tolist()
    pred = int(torch.argmax(logits, dim=-1).item())
    return {"label": pred, "p_valid": probs[0], "p_hoax": probs[1]}

@app.post("/predict")
@torch.inference_mode()
def predict(item: Item):
    return _predict_single(item.text)

@app.get("/supported_sources")
def supported_sources():
    # tampilkan uniq tanpa www.
    uniq = sorted(set(SUPPORTED_DOMAINS))
    display = sorted(set([d.replace("www.","") for d in uniq]))
    return {"domains": display}

@app.post("/extract")
async def extract_url(payload: URLIn):
    try:
        ext = await extract_article(str(payload.url))
        return {"text": ext.text, "source": ext.source, "length": ext.length, "preview": ext.preview}
    except ValueError as e:
        # error validasi / ekstraksi yang bisa dipahami user
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Gagal mengambil/ekstrak artikel: {e}")

@app.post("/predict_url", response_model=PredictOut)
async def predict_url(payload: URLIn):
    try:
        ext = await extract_article(str(payload.url))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Gagal mengambil/ekstrak artikel: {e}")

    pred = _predict_single(ext.text)
    return PredictOut(
        label=pred["label"],
        p_valid=float(pred["p_valid"]),
        p_hoax=float(pred["p_hoax"]),
        source=ext.source.replace("www.",""),
        extracted_chars=ext.length,
        preview=ext.preview
    )
