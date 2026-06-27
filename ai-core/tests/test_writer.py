# tests/test_writer.py
import pytest
from app.core.writer import validate_outreach_draft, run_writer_agent
from app.schemas.api_models import DraftInput, DraftLeadDetail, ExtractedSignals, EnrichedData

# 1. Test bộ đếm từ và placeholder tĩnh bằng Python
def test_validate_outreach_draft_discord_limits():
    # Tin nhắn dài hơn 150 từ cho kênh Discord -> word_count_ok phải False
    long_draft = "test " * 160
    word_count, has_placeholder, word_count_ok = validate_outreach_draft(long_draft, "discord")
    assert word_count == 160
    assert word_count_ok is False
    assert has_placeholder is False

    # Tin nhắn chứa dấu placeholder ngoặc vuông
    placeholder_draft = "Hey [Name], please check this."
    _, has_placeholder, _ = validate_outreach_draft(placeholder_draft, "email")
    assert has_placeholder is True


# 2. Test gọi thực tế Writer Agent bằng Gemini (Integration Test)
@pytest.mark.asyncio
@pytest.mark.integration
async def test_writer_agent_integration_discord():
    payload = DraftInput(
        lead_id="test_writer_123",
        source="discord",
        classification="HOT",
        score=95,
        extracted_signals=ExtractedSignals(
            role_type="TECH",
            specific_error="Stripe to Airtable integration fails in n8n with 500 error",
            tool="n8n",
            tech_stack=["n8n", "Airtable", "Stripe"],
            request_summary="Fix Stripe webhook sync error"
        ),
        lead=DraftLeadDetail(
            author="Alex",
            content="My n8n workflow syncing Stripe to Airtable keeps throwing 500. Help needed."
        ),
        enriched_data=EnrichedData(
            company_domain="acme.com",
            company_description="B2B automated platform"
        )
    )

    response = await run_writer_agent(payload)
    
    assert response.lead_id == "test_writer_123"
    assert response.channel_source == "discord"
    assert response.validation.has_placeholder is False  # Tuyệt đối không chứa [...]
    assert response.validation.word_count_ok is True      # Phải <= 150 từ
    
    # Kiểm tra xem có chứa giọng sales hay rủ meeting không
    draft_lower = response.generated_draft.lower()
    assert "zoom" not in draft_lower
    assert "schedule a call" not in draft_lower
    assert "book a meeting" not in draft_lower
def test_validate_outreach_draft_email_limits():
    # BA confirm (2026-06): HN/Email limit là 200 từ, không phải 250.
    # Test đúng tại biên — dùng số lớn hơn nhiều (ví dụ 260) sẽ pass cả limit
    # cũ (250) và mới (200), không phát hiện được regression nếu ai sửa nhầm.
    exactly_limit = "word " * 200
    wc, hp, ok = validate_outreach_draft(exactly_limit, "email")
    assert wc == 200
    assert ok is True  # đúng 200 từ -> vẫn hợp lệ (boundary inclusive)

    over_limit = "word " * 201
    wc, hp, ok = validate_outreach_draft(over_limit, "email")
    assert wc == 201
    assert ok is False


def test_validate_outreach_draft_subject_not_counted():
    # BA confirm: dòng "Subject:" của email/HN KHÔNG tính vào giới hạn từ,
    # chỉ tính phần body. Nếu code regress về tính cả Subject vào word_count,
    # tổng sẽ là 209 (> 200) và assert ok is True dưới đây sẽ fail.
    subject_line = "Subject: Quick fix for your n8n Stripe-to-Airtable 500 error\n\n"
    body = "word " * 200
    draft = subject_line + body

    wc, hp, ok = validate_outreach_draft(draft, "email")
    assert wc == 200
    assert ok is True
    assert hp is False

def test_validate_fallback_forum():
    short_msg = "word " * 140
    _, _, ok = validate_outreach_draft(short_msg, "reddit")
    assert ok is True  # forum → 150 limit

@pytest.mark.asyncio
@pytest.mark.integration
async def test_writer_agent_missing_author_fallback():
    payload = DraftInput(
        lead_id="test_fallback_001",
        source="discord",
        classification="WARM",
        score=65,
        extracted_signals=ExtractedSignals(
            role_type="NON_TECH",
            specific_error=None,
            tool="zapier",
            tech_stack=["Zapier"],
            request_summary="Automate invoice sending"
        ),
        lead=DraftLeadDetail(
            author=None,  # ← thiếu tên → phải fallback "Hey there"
            content="Need help automating invoices with Zapier"
        ),
        enriched_data=None  # ← thiếu website → không hallucinate ngành
    )
    response = await run_writer_agent(payload)
    assert response.variables_used.name == "Hey there"
    assert response.validation.has_placeholder is False
    assert response.validation.word_count_ok is True
    # NON_TECH → không có jargon kỹ thuật
    draft_lower = response.generated_draft.lower()
    assert "webhook" not in draft_lower


# 3. Regression test cho Bug #Lỗi-3 (Joint Test 2026-06-18):
# System prompt cũ ở Rule 4 (ZERO PLACEHOLDERS IN OUTPUT) gợi ý LLM ký tên
# "SPORTAIV Team" ở cuối draft ("Sign off naturally... or 'SPORTAIV Team'"),
# mâu thuẫn trực tiếp với Rule 1 (PEER-TO-PEER TONE, cấm lộ identity công ty).
# Output thật từ Joint Test: draft kết thúc bằng "Best,\nSPORTAIV Team".
# Sau khi sửa Rule 4 (bỏ hẳn yêu cầu sign-off), draft không được chứa bất kỳ
# dấu hiệu ký tên công ty hoặc closing salutation nào — kể cả khi không kèm
# tên công ty (ví dụ chỉ "Best," hoặc "Cheers," cũng vẫn phá vỡ tone peer-to-peer).
@pytest.mark.asyncio
@pytest.mark.integration
async def test_writer_agent_no_signature_or_company_name():
    payload = DraftInput(
        lead_id="test_signature_001",
        source="discord",
        classification="HOT",
        score=85,
        extracted_signals=ExtractedSignals(
            role_type="TECH",
            specific_error="n8n workflow losing data packets during peak hours",
            tool="n8n",
            tech_stack=["n8n", "Supabase", "webhooks"],
            request_summary="Fix data loss in n8n webhook integration"
        ),
        lead=DraftLeadDetail(
            author="huydang07648",
            content="Our n8n automation is losing data packets during peak hours."
        ),
        enriched_data=None
    )

    response = await run_writer_agent(payload)
    draft_lower = response.generated_draft.lower()

    # Không được lộ identity công ty dưới mọi hình thức
    assert "sportaiv" not in draft_lower

    # Không được có closing salutation kiểu sign-off, kể cả không kèm tên công ty
    for closing in ["best,", "cheers,", "regards,", "sincerely,", "best regards"]:
        assert closing not in draft_lower