# tests/test_scorer.py
import pytest
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient

from app.main import app
from app.utils.money_parser import extract_max_budget
from app.utils.dq_filters import run_pre_filters
from app.core.scorer import calculate_python_score, ExtractedSignals

client = TestClient(app)

# =====================================================================
# TẦNG 1: UNIT TEST CHO MONEY PARSER (B1 & VIP WHALE)
# =====================================================================

@pytest.mark.parametrize(
    "text,expected_budget",
    [
        # Kiểm thử định dạng viết tắt K
        ("We have a 5k budget for this task", 5000),
        ("Looking for help with a budget of 1.5K USD", 1500),
        ("Project budget: 10k", 10000),
        # Kiểm thử định dạng số thông thường và dấu phân cách
        ("Our budget is $300 to fix this bug", 300),
        ("The total contract value is $1,500", 1500),
        ("European style formatting: $10.000 for development", 10000),
        ("No spaces formatting: $15000 max", 15000),
        # Kiểm thử các trường hợp không có budget hoặc số không hợp lệ
        ("Just looking for some recommendations, no paid gig.", None),
        ("We use n8n for our daily operations.", None),
        ("The year is 2026", None)
    ]
)
def test_money_parser_extraction(text, expected_budget):
    assert extract_max_budget(text) == expected_budget


# =====================================================================
# TẦNG 2: UNIT TEST CHO LOGIC TÍNH ĐIỂM PYTHON (ICP SCORING RULES)
# =====================================================================

def test_scoring_standard_case_no_overrides():
    # A1 (+25), B3 (+10), C1 (+20) -> Tổng: 55
    score, details = calculate_python_score(["A1", "B3", "C1"])
    assert score == 55
    assert details["group_a"].score == 25
    assert details["group_b"].score == 10
    assert details["group_c"].score == 20


def test_scoring_override_a3_overrides_a1():
    score, details = calculate_python_score(["A1", "A3", "B1", "C3"])
    assert score == 75
    assert details["group_a"].score == 30           # ← A3(30) được tính
    assert "A3" in details["group_a"].signals_detected  # ← A3 detected
    assert "A1" in details["group_a"].signals_detected  # ← A1 detected (audit)
    # KHÔNG có assert "A1" not in ... vì PRD yêu cầu giữ lại cho audit


def test_scoring_override_a1_a2_no_a3():
    # Chỉ có A1 (+25) và A2 (+20), không có A3 -> Chỉ lấy mã nặng nhất là A1 (+25)
    # B2 (+15), C2 (+15) -> Tổng: 25 + 15 + 15 = 55
    score, details = calculate_python_score(["A1", "A2", "B2", "C2"])
    assert score == 55
    assert details["group_a"].score == 25


def test_scoring_penalties():
    # A4 (-20), B5 (-15), C4 (-20) -> Tổng: -55
    score, details = calculate_python_score(["A4", "B5", "C4"])
    assert score == -55


def test_scoring_additive_a2_a3_no_a1():
    # BA confirm (2026-06): A2 (tech stack trùng khớp) và A3 (tuyển dụng trực
    # tiếp) là 2 tín hiệu độc lập, KHÔNG bị coi là trùng lặp ngữ cảnh khẩn cấp
    # như cặp A1/A3 -> cộng dồn cả hai: 20 + 30 = 50.
    score, details = calculate_python_score(["A2", "A3"])
    assert score == 50
    assert details["group_a"].score == 50
    assert set(details["group_a"].signals_detected) == {"A2", "A3"}


def test_scoring_a1_a2_a3_together():
    # Case giao giữa 2 quy tắc override: A3 vẫn override A1 (loại A1 khỏi
    # điểm, giữ lại để audit), nhưng A2 không bị động tới vì rule loại A2 chỉ
    # áp dụng khi có A1 VÀ không có A3 -> điểm cuối = A2(20) + A3(30) = 50.
    score, details = calculate_python_score(["A1", "A2", "A3"])
    assert score == 50
    assert details["group_a"].score == 50
    assert "A1" in details["group_a"].signals_detected  # giữ lại để audit
    assert "A2" in details["group_a"].signals_detected
    assert "A3" in details["group_a"].signals_detected


# =====================================================================
# TẦNG 3: INTEGRATION TEST CHO ROUTE POST /score (MOCKED AGENT)
# =====================================================================

@patch("app.api.score.run_scorer_agent", new_callable=AsyncMock)
def test_route_score_vip_whale_bypasses_dq(mock_scorer):
    """
    VIP Whale (Budget >= $10k) phải bypass được toàn bộ disqualifiers từ
    cả Python pre-filter (NDA) lẫn Scorer Agent (DQ1, DQ3, DQ4).
    """
    # Giả lập Scorer Agent trả về kết quả dính DQ1 và DQ3
    mock_scorer.return_value = (
        True,                                # is_dq
        "DQ1: Delivery < 48h",              # dq_reason
        None,                                # total_score
        None,                                # scoring_details
        None                                 # extracted_signals
    )
    
    # Gửi tin nhắn dính NDA và làm gấp nhưng budget rất lớn ($15,000)
    payload = {
        "lead_id": "test_vip_123",
        "source": "discord",
        "content": "Need an immediate integration in 24 hours. Must sign NDA first. Budget is $15,000."
    }
    
    response = client.post("/score", json=payload)
    assert response.status_code == 200
    
    data = response.json()
    assert data["lead_id"] == "test_vip_123"
    assert data["status"] == "scored"  # Vẫn được score
    assert data["is_disqualified"] is False  # Không bị loại bỏ
    assert data["is_vip_whale"] is True
    assert data["classification"] == "HOT"  # VIP Whale tự động HOT


@patch("app.api.score.run_scorer_agent", new_callable=AsyncMock)
def test_route_score_disqualified_by_pre_filter_nda(mock_scorer):
    """
    Nếu không phải VIP Whale, dính NDA ở pre-filter phải bị loại lập tức
    mà không cần gọi tới Scorer Agent (Gemini).
    """
    payload = {
        "lead_id": "test_dq_nda",
        "source": "hackernews",
        "content": "Please sign this NDA before I share the workflow details."
    }
    
    response = client.post("/score", json=payload)
    assert response.status_code == 200
    
    data = response.json()
    assert data["status"] == "disqualified"
    assert data["is_disqualified"] is True
    assert "DQ2" in data["disqualification_reason"]
    
    # Xác nhận không cần tốn API call gọi tới LLM Scorer Agent
    mock_scorer.assert_not_called()


@patch("app.api.score.run_scorer_agent", new_callable=AsyncMock)
def test_route_score_disqualified_by_agent_timeline(mock_scorer):
    """
    Scorer Agent phát hiện yêu cầu hoàn thành dự án gấp trong 24 giờ (DQ1)
    và không có budget lớn để bypass -> Bị loại.
    """
    # Giả lập Agent phát hiện dính DQ1
    mock_scorer.return_value = (
        True,
        "DQ1: Timeline hoàn thành < 48h",
        None, None, None
    )
    
    payload = {
        "lead_id": "test_dq_timeline",
        "source": "forum",
        "content": "I need a complete functional SaaS prototype working by tomorrow night."
    }
    
    response = client.post("/score", json=payload)
    assert response.status_code == 200
    
    data = response.json()
    assert data["status"] == "disqualified"
    assert data["is_disqualified"] is True
    assert "DQ1" in data["disqualification_reason"]


@patch("app.api.score.run_scorer_agent", new_callable=AsyncMock)
def test_route_score_successful_warm_lead(mock_scorer):
    """
    Kiểm tra một lead bình thường, được chấm điểm và trả về phân loại WARM thành công.
    """
    # Giả lập Agent trả về phân tích thành công
    mock_scorer.return_value = (
        False,  # is_dq
        None,   # dq_reason
        65,     # total_score (Thuộc ngưỡng WARM: 55-79)
        {
            "group_a": {"signals_detected": ["A1"], "score": 25, "reason": "Cộng dồn tự nhiên"},
            "group_b": {"signals_detected": ["B3"], "score": 10, "reason": "Cộng dồn tự nhiên các tín hiệu Budget"},
            "group_c": {"signals_detected": ["C3"], "score": 20, "reason": "Cộng dồn tự nhiên các tín hiệu Tech Fit"}
        },
        ExtractedSignals(
            role_type="TECH",
            specific_error="LangGraph memory state reset issue",
            tool="LangGraph",
            tech_stack=["LangGraph", "Python"],
            request_summary="Fix state tracking in multi-agent setup",
            needs_enrichment=True
        )
    )
    
    payload = {
        "lead_id": "test_warm_lead",
        "source": "discord",
        "content": "My LangGraph agent keeps losing its thread memory. Need help ASAP."
    }
    
    response = client.post("/score", json=payload)
    assert response.status_code == 200
    
    data = response.json()
    assert data["status"] == "scored"
    assert data["total_score"] == 65
    assert data["classification"] == "WARM"
    assert data["extracted_signals"]["needs_enrichment"] is True