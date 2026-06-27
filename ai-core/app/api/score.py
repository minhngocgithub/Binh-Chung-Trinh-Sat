# app/api/score.py
from fastapi import APIRouter, HTTPException
from app.schemas.api_models import LeadInput, ScoreResponse
from app.utils.dq_filters import run_pre_filters
from app.core.scorer import run_scorer_agent

router = APIRouter()


def _classify(score: int | None) -> str | None:
    if score is None:
        return None
    if score >= 80: return "HOT"
    if score >= 55: return "WARM"
    if score >= 25: return "COLD"
    return "DISCARD"


@router.post("/score", response_model=ScoreResponse)
async def score_lead(lead: LeadInput):
    # Layer 1: Python pre-filter — DQ2 (NDA) + VIP Whale, không cần LLM
    pre = run_pre_filters(content=lead.content, title=lead.title or "")

    if pre["is_disqualified"]:
        return ScoreResponse(
            lead_id=lead.lead_id,
            status="disqualified",
            is_disqualified=True,
            disqualification_reason=pre["disqualification_reason"],
            source=lead.source,
            title=lead.title,
            content=lead.content,
            url=lead.url,
            author=lead.author,
            created_at=lead.created_at,
            intent_matched=lead.intent_matched,
            forum_labels=lead.forum_labels,
        )

    is_vip = pre["is_vip_whale"]

    # Layer 2: Scorer Agent — LLM bóc tách tín hiệu, Python tính điểm.
    try:
        is_dq, dq_reason, total_score, scoring_details, extracted = await run_scorer_agent(
            content=lead.content,
            title=lead.title or "",
            budget_detected=pre["detected_budget"],
        )
    except RuntimeError as e:
        raise HTTPException(
            status_code=503,
            detail=f"Scorer Agent tạm thời không khả dụng: {str(e)}"
        )

    # VIP Whale bypass DQ1/DQ3/DQ4 — business rule nằm ở API layer, không nằm trong scorer
    if is_vip:
        return ScoreResponse(
            lead_id=lead.lead_id,
            status="scored",
            is_disqualified=False,
            is_vip_whale=True,
            total_score=total_score,
            classification="HOT",       # VIP luôn HOT, không cần threshold
            scoring_details=scoring_details,
            extracted_signals=extracted,
            source=lead.source,
            title=lead.title,
            content=lead.content,
            url=lead.url,
            author=lead.author,
            created_at=lead.created_at,
            intent_matched=lead.intent_matched,
            forum_labels=lead.forum_labels,
        )

    # Non-VIP: nếu DQ1/DQ3/DQ4 → loại
    # [FIX BUG #6]: Vẫn phải trả về đầy đủ thông tin điểm số kèm thông tin thô khi dính DQ
    if is_dq:
        return ScoreResponse(
            lead_id=lead.lead_id,
            status="disqualified",
            is_disqualified=True,
            disqualification_reason=dq_reason,
            total_score=total_score,
            classification="DISCARD",
            scoring_details=scoring_details,
            extracted_signals=extracted,
            source=lead.source,
            title=lead.title,
            content=lead.content,
            url=lead.url,
            author=lead.author,
            created_at=lead.created_at,
            intent_matched=lead.intent_matched,
            forum_labels=lead.forum_labels,
        )

    # Normal lead: phân loại theo threshold
    return ScoreResponse(
        lead_id=lead.lead_id,
        status="scored",
        is_disqualified=False,
        is_vip_whale=False,
        total_score=total_score,
        classification=_classify(total_score),
        scoring_details=scoring_details,
        extracted_signals=extracted,
        source=lead.source,
        title=lead.title,
        content=lead.content,
        url=lead.url,
        author=lead.author,
        created_at=lead.created_at,
        intent_matched=lead.intent_matched,
        forum_labels=lead.forum_labels,
    )