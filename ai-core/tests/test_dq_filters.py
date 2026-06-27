from app.utils.dq_filters import run_pre_filters, check_vip_whale, check_nda


def test_vip_whale_comma_format():
    is_vip, budget = check_vip_whale("Budget is around $10,000 for this project")
    assert is_vip is True
    assert budget == 10000


def test_vip_whale_dot_format():
    is_vip, budget = check_vip_whale("Budget: $15.000")
    assert is_vip is True
    assert budget == 15000


def test_vip_whale_no_separator():
    is_vip, budget = check_vip_whale("We have $12000 to spend")
    assert is_vip is True
    assert budget == 12000


def test_not_vip_low_budget():
    """
    Regression:
    Budget vẫn phải được giữ lại để scorer có thể inject B1.
    """
    is_vip, budget = check_vip_whale("Willing to pay $400")

    assert is_vip is False
    assert budget == 400


def test_prefilter_preserves_detected_budget():
    """
    Regression:
    detected_budget không được mất khi lead không phải VIP.
    """
    result = run_pre_filters(
        content="Need help with n8n. Budget is $400",
        title=""
    )

    assert result["is_vip_whale"] is False
    assert result["detected_budget"] == 400


def test_nda_detected():
    assert check_nda("You must sign an NDA before we discuss further") is True


def test_asap_does_not_trigger_dq():
    """
    Quan trọng: PRD Example 1 (Hot Lead) chứa 'asap' nhưng KHÔNG bị loại.
    Test này đảm bảo pre-filter không vô tình bắt 'asap' làm DQ.
    """
    result = run_pre_filters(
        content="n8n memory overflow, asap, willing to pay $400",
        title=""
    )

    assert result["is_disqualified"] is False


def test_nda_first_contact_disqualified():
    result = run_pre_filters(
        content="Before we talk, you need to sign an NDA",
        title=""
    )

    assert result["is_disqualified"] is True
    assert result["disqualification_reason"] == "DQ2: NDA required from first contact"


def test_vip_whale_bypasses_everything():
    """
    VIP Whale phải bypass dù có chứa NDA
    """
    result = run_pre_filters(
        content="Budget $20,000, but need an NDA signed first",
        title=""
    )

    assert result["is_disqualified"] is False
    assert result["is_vip_whale"] is True
    assert result["detected_budget"] == 20000