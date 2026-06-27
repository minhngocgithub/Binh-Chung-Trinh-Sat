# app/main.py
from fastapi import FastAPI
from app.api import score, draft

app = FastAPI(title="Binh Chung Trinh Sat - AI Core")

app.include_router(score.router)
app.include_router(draft.router)

@app.get("/health")
def health():
    return {"status": "ok"}