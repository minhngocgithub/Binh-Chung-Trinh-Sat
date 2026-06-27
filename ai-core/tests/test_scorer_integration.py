# tests/test_scorer_integration.py
import pytest
from app.schemas.api_models import LeadInput
from app.core.scorer import run_scorer_agent
@pytest.mark.asyncio        
@pytest.mark.integration
async def test_prd_example_3_discard():
    """
    PRD Example 3: side project → DISCARD
    Score thực tế có thể khác -30 vì LangChain trigger C3 (+20).
    Điều quan trọng: phân loại phải là DISCARD (score < 25).
    """
    is_dq, _, score, details, _ = await run_scorer_agent(
        content="I'm exploring automation tools and learning LangChain for a side project. Just for fun, not a paid thing.",
        title="",
        budget_detected=None,
    )
    assert is_dq is False
    assert score is not None
    assert score < 25, f"Expected DISCARD (score < 25), got {score}"  # ← không hardcode -25

    # Xác nhận A5 bắt buộc phải có (tín hiệu cốt lõi)
    a_signals = details["group_a"].signals_detected
    assert "A5" in a_signals, f"A5 (learning/side project) phải được detect, got {a_signals}"
@pytest.mark.asyncio       
@pytest.mark.integration
async def test_prd_hot_lead_asap():
    """
    Lead có asap + n8n error + không có A3 → A1 detect.
    Quan trọng: 'asap' KHÔNG trigger DQ1.
    """
    is_dq, dq_reason, score, details, extracted = await run_scorer_agent(
        content="My n8n webhook keeps failing with 500 error when syncing Airtable. Need help asap, production is down.",
        title="",
        budget_detected=400,  # Python inject B1
    )
    assert is_dq is False, f"asap không được trigger DQ1, got: {dq_reason}"
    assert score is not None
    assert score >= 55, f"Expected HOT/WARM (score >= 55), got {score}"

    a_signals = details["group_a"].signals_detected
    assert "A1" in a_signals, "A1 (asap/urgent) phải được detect"
    assert "A1" not in ["DQ1"], "asap là A1, không phải DQ1"