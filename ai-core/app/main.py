from fastapi import FastAPI, Depends
from app.api import score, draft
from app.core.auth import verify_request

app = FastAPI(title="Binh Chung Trinh Sat - AI Core")

# /health không cần auth — dùng để monitor
@app.get("/health")
def health():
    return {"status": "ok"}

app.include_router(score.router, dependencies=[Depends(verify_request)])
app.include_router(draft.router, dependencies=[Depends(verify_request)])