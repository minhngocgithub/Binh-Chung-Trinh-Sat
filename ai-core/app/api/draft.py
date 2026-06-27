# app/api/draft.py
from fastapi import APIRouter, HTTPException
from app.schemas.api_models import DraftInput, DraftResponse
from app.core.writer import run_writer_agent

router = APIRouter()

@router.post("/draft", response_model=DraftResponse)
async def create_draft(payload: DraftInput):
    try:
        response = await run_writer_agent(payload)
        return response
    except ValueError as e:
        # Validation fail trong writer.py (placeholder sót hoặc vượt giới hạn từ)
        # -> lỗi nội dung draft, Dev1 cần biết để retry, KHÔNG phải lỗi hệ thống
        raise HTTPException(status_code=422, detail=str(e))
    except RuntimeError as e:
        # Writer Agent / LLM provider lỗi (rate limit, timeout, quota hết)
        raise HTTPException(
            status_code=503,
            detail=f"Writer Agent tạm thời không khả dụng: {str(e)}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Writer Agent failed: {str(e)}"
        )