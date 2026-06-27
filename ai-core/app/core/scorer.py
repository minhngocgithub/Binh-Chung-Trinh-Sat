from typing import List, Optional, Literal, Tuple
from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel

from app.schemas.api_models import ExtractedSignals, SignalDetail
from app.core.fallback import run_with_fallback
from app.core.llm import get_primary_model


_MAX_CONTENT_CHARS = 1500


# ---------------------------------------------------------------------------
# 1. Schema trả về từ LLM Scorer Agent
# ---------------------------------------------------------------------------

class LLMScorerOutput(BaseModel):
    dq1_timeline_under_48h: bool = Field(
        description=(
            "Chỉ chọn True nếu khách hàng BẮT BUỘC bàn giao sản phẩm hoàn thiện dưới 48 giờ. "
            "ASAP hoặc khẩn cấp sửa lỗi KHÔNG kích hoạt bộ lọc này."
        )
    )
    dq2_nda_required: bool = Field(
        description=(
            "True nếu bài đăng yêu cầu ký NDA hoặc thỏa thuận bảo mật BẮT BUỘC trước khi "
            "bắt đầu bất kỳ cuộc trao đổi nào về dự án. "
            "Ví dụ DQ2: 'Must sign NDA before I share details', 'require confidentiality agreement first'. "
            "KHÔNG phải DQ2: kỳ vọng bảo mật chuyên nghiệp thông thường, đề cập NDA trong quá trình làm việc."
        )
    )
    dq3_is_subcontractor_search: bool = Field(
        description="True nếu người đăng là Agency hoặc Dev tìm Sub-contractor để làm thay phần việc của họ."
    )
    dq4_violates_tos: bool = Field(
        description="True nếu bài viết vi phạm điều khoản sử dụng, spam sản phẩm không liên quan, hoặc lừa đảo."
    )
    detected_signals: List[str] = Field(
        description=(
            "Mảng các nhãn ICP phát hiện được từ văn bản. "
            "Có thể chọn: A1, A2, A3, A4, A5, B2, B3, B4, B5, C1, C2, C3, C4, C5. "
            "KHÔNG BAO GIỜ chọn B1 — budget amount được Python xử lý riêng qua regex, "
            "nếu bạn trả về B1 nó sẽ bị loại bỏ ngay lập tức."
        )
    )
    role_type: Literal["TECH", "NON_TECH"] = Field(
        description="TECH nếu họ tự code, tự cấu hình n8n/API. NON_TECH nếu họ là Founder/PM phi kỹ thuật chỉ nêu yêu cầu nghiệp vụ."
    )
    specific_error: Optional[str] = Field(None, description="Lỗi cụ thể hoặc lỗi kỹ thuật họ đang gặp phải.")
    tool: Optional[str] = Field(None, description="Công cụ chính đang được nhắc tới (n8n, LangGraph, Airtable, etc.).")
    tech_stack: List[str] = Field(default_factory=list, description="Danh sách các công nghệ xuất hiện trong bài viết.")
    request_summary: Optional[str] = Field(None, description="Tóm tắt ngắn gọn yêu cầu chính của họ (tối đa 1-2 câu).")


# ---------------------------------------------------------------------------
# 2. Khởi tạo Agent — dùng get_primary_model() thay vì hardcode provider
#    run_with_fallback() sẽ override model khi gọi thật
# ---------------------------------------------------------------------------

scorer_agent = Agent(
    model=get_primary_model(),
    output_type=LLMScorerOutput,
    system_prompt="""
You are the Lead Analyst for SPORTAIV. Your job is to analyze incoming text from developers, founders, and community posts, identify hard disqualifiers (DQs), and extract ICP signal codes.

CRITICAL RULES FOR SIGNALS:
- DO NOT perform any math. Just identify if the signal criteria are met.
- Signals within the same group are NOT mutually exclusive unless explicitly stated below.
  If multiple codes independently apply to the same text, tag ALL of them — do not collapse
  into a single "most representative" code. Override logic (e.g. A3 overrides A1) is handled
  DOWNSTREAM in Python; your job is ONLY to report every signal that genuinely applies.
  EXAMPLE: A post that is hiring (A3), uses urgent language (A1), AND asks a specific technical
  question (A2) → you MUST report all three: [A1, A2, A3]. Python resolves the priority.

- Group A (Intent):
  * A1: Production is actively failing or the person is stuck and explicitly needs help ASAP.
    The urgency is about fixing something NOW — they are NOT hiring.
    Keywords: asap, urgent, need help now, stuck, desperate, critical, breaking, production down,
    time-sensitive, emergency, can't proceed.
    IMPORTANT: "Fix this ASAP" = A1. Do NOT confuse with DQ1 ("deliver entire project in 48h").
  * A2: Asks a SPECIFIC technical question about a named automation tool or AI workflow.
    The question must reference a concrete tool or specific technical behavior.
    Keywords: how do I [tool], [tool] not working, error in [tool], failing, broken,
    help with [specific tool], [tool] keeps throwing, debug, troubleshoot, configure.
    IMPORTANT: A2 requires a specific tool or workflow context — general "what is X" or
    "which tool is better" questions are A4, NOT A2.
  * A3: Specifically looking to hire someone or pay for the work to be done.
    Keywords: looking for, hiring, need a freelancer, paid gig, budget, contract,
    willing to pay, seeking contractor, developer needed, open to offers, compensation.
  * A4: Asking for general opinions, comparing tools, or theoretical questions with no hiring signal.
    Keywords: what is, anyone know about, thoughts on, best tool for, comparison,
    which is better, recommend a tool, versus, pros and cons.
  * A5: Clearly a learning, hobby, or academic exercise — no commercial intent.
    Keywords: learning, side project, just exploring, for fun, university, course,
    student project, practice, experimenting.

- Group B (Budget / ICP Level):
  IMPORTANT: Do NOT assign B1 — budget amount detection (≥ $300) is handled EXCLUSIVELY
  by the Python layer via regex. If you include B1, it will be stripped from your output.

  * B2: Explicitly mentions company details indicating an active, operating business.
    Required evidence: a company domain, stated revenue, order volume, user count, or explicit
    company name with operational context (e.g. "our SaaS has 500 users", "acmecorp.com").
    STRICT NEGATIVE RULE — do NOT assign B2 for any of these alone:
      - Merely mentioning a budget number ("budget $500")
      - Mentioning a tool name ("need n8n expert")
      - Using the word "client" in a personal context ("I fix client setups")
  * B3: The post body clearly identifies the poster as a Founder, CTO, PM, or Business Owner.
    STRICT NEGATIVE RULE — do NOT assign B3 for any of these alone:
      - Raw usernames (e.g. "bunkat", "urgent_client") without explicit role stated in the post.
      - Saying "we are building" or "our team" without indicating the poster's own leadership role.
    B3 requires personal role evidence in the text: "I am the founder", "As the CTO of...",
    "Our PM asked me to post".
  * B4: Assign ONLY when B2, B3, and B5 are all absent AND there is no mention of money,
    company, or role. This is the neutral default when no budget signal is present.
  * B5: Explicitly states limited or no budget for this project.
    Keywords: no budget yet, bootstrapping, pre-revenue, just started, tight budget,
    can't afford, looking for free solutions.

- Group C (Tech Fit):
  * C1: Mentions any low-code/no-code automation platform by its product name.
    Platforms: n8n, make.com, zapier, airtable, notion api, webhook / webhooks,
    integromat, activepieces, pipedream, pabbly connect.
    STRICT NEGATIVE RULE — do NOT assign C1 for:
      - The common English verb "make" (e.g., "to make improvements", "make it work",
        "make a decision"). C1 only applies when "Make" clearly refers to the software.
  * C2: The task involves API integration, data synchronization, workflow automation, or
    automated notifications — regardless of which specific tools are mentioned.
    Keywords: connect API, sync data, automate workflow, send notification, trigger,
    integrate [A] with [B], pull data from, push data to, scheduled task, event-driven.
    NOTE: C1 and C2 are INDEPENDENT. Naming a tool (C1) AND describing an integration
    task (C2) → tag BOTH.
    Example: "sync inventory across Shopify, Airtable, and Make.com" → [C1, C2].
  * C3: Mentions advanced AI agent frameworks or custom multi-agent architectures.
    Frameworks: langgraph, crewai, autogen, multi-agent, agentic workflow,
    RL (reinforcement learning), LLM fine-tuning, llamaindex, custom agent pipeline.
  * C4: The PRIMARY requirement is heavy DevOps or ML infrastructure with no automation angle.
    Keywords: kubernetes, terraform, self-hosted model, GPU cluster, docker swarm,
    mlops, model serving, bare metal, HPC, distributed training.
  * C5: The PRIMARY requirement is a mobile app or heavy frontend UI — not workflow automation.
    Keywords: react native, flutter, ios app, android, swift, kotlin,
    full stack frontend, UI/UX, mobile-first.

CRITICAL RULES FOR HARD DISQUALIFIERS (DQs):
- DQ1 (Delivery < 48h): True ONLY if they demand the ENTIRE completed deliverable in under
  48 hours. Example: "Need full app done by tomorrow morning" = DQ1.
  "Fix this bug ASAP" = A1, NOT DQ1.
- DQ2 (NDA before any conversation): True if the post EXPLICITLY requires signing an NDA,
  non-disclosure, or confidentiality agreement BEFORE any project discussion begins.
  Example: "Must sign NDA before I share details" = DQ2.
  General professional confidentiality expectations are NOT DQ2. Mentioning NDA as a
  future step in the engagement is NOT DQ2.
- DQ3 (Subcontractor search): True if the poster is a developer or agency outsourcing
  their client's task to another contractor.
  Signs: "I have a client who needs...", "need someone to do this for my client",
  agency language with subcontracting intent.
- DQ4 (ToS violation): Spam, irrelevant advertising, offensive content, scam.
""",
    retries=3,
)


# ---------------------------------------------------------------------------
# 3. Logic tính điểm Python thuần
# ---------------------------------------------------------------------------

def calculate_python_score(detected_signals: List[str]) -> Tuple[int, dict[str, SignalDetail]]:
    valid_signals = set(detected_signals)

    # --- Group A ---
    a_signals = [s for s in valid_signals if s.startswith("A")]
    final_a = list(a_signals)
    a_reason = "Cộng dồn tự nhiên"

    if "A3" in final_a and "A1" in final_a:
        final_a.remove("A1")
        a_reason = "A3 override A1 (Có A3 nên không tính điểm A1)"
    elif "A1" in final_a and "A2" in final_a and "A3" not in final_a:
        final_a.remove("A2")
        a_reason = "Chỉ lấy A1 vì không có A3"

    a_weights = {"A1": 25, "A2": 20, "A3": 30, "A4": -20, "A5": -30}
    a_score = sum(a_weights.get(s, 0) for s in final_a)

    # --- Group B ---
    b_signals = [s for s in valid_signals if s.startswith("B")]
    b_weights = {"B1": 25, "B2": 15, "B3": 10, "B4": 0, "B5": -15}
    b_score = sum(b_weights.get(s, 0) for s in b_signals)
    b_reason = "Cộng dồn tự nhiên các tín hiệu Budget"

    # --- Group C ---
    c_signals = [s for s in valid_signals if s.startswith("C")]
    c_weights = {"C1": 20, "C2": 15, "C3": 20, "C4": -20, "C5": -15}
    c_score = sum(c_weights.get(s, 0) for s in c_signals)
    c_reason = "Cộng dồn tự nhiên các tín hiệu Tech Fit"

    total_score = a_score + b_score + c_score

    scoring_details = {
        # Dùng final_a (sau khi áp dụng override rule), không phải a_signals (gốc từ LLM).
        # Đảm bảo signals_detected khớp chính xác với những signal thực sự contribute vào a_score.
        "group_a": SignalDetail(signals_detected=final_a, score=a_score, reason=a_reason),
        "group_b": SignalDetail(signals_detected=b_signals, score=b_score, reason=b_reason),
        "group_c": SignalDetail(signals_detected=c_signals, score=c_score, reason=c_reason),
    }

    return total_score, scoring_details


# ---------------------------------------------------------------------------
# 4. Entry point — gọi LLM qua fallback chain
# ---------------------------------------------------------------------------

async def run_scorer_agent(
    content: str,
    title: str = "",
    budget_detected: Optional[int] = None,
) -> Tuple[bool, Optional[str], int, dict[str, SignalDetail], ExtractedSignals]:
    full_text = f"{title} {content}".strip()

    if len(full_text) > _MAX_CONTENT_CHARS:
        full_text = full_text[:_MAX_CONTENT_CHARS] + "...[truncated]"

    try:
        result = await run_with_fallback(scorer_agent, full_text, retries_per_model=1)
        analysis: LLMScorerOutput = result.output
    except Exception as e:
        raise RuntimeError(f"Lỗi khi thực thi Pydantic AI Agent: {str(e)}") from e

    # DQ2 từ LLM là lớp bảo vệ thứ hai — lớp đầu tiên là Python regex trong dq_filters.py.
    # Nếu Python regex đã bắt DQ2, LLM sẽ không được gọi (pre-filter return sớm).
    # Nếu LLM cũng phát hiện NDA language mà regex bỏ sót → bắt ở đây.
    dq_reason: Optional[str] = None
    if analysis.dq1_timeline_under_48h:
        dq_reason = "DQ1: Hoàn thành dự án < 48h"
    elif analysis.dq2_nda_required:
        dq_reason = "DQ2: Yêu cầu ký NDA ngay từ đầu (phát hiện bởi LLM)"
    elif analysis.dq3_is_subcontractor_search:
        dq_reason = "DQ3: Agency tìm kiếm nhà thầu phụ"
    elif analysis.dq4_violates_tos:
        dq_reason = "DQ4: Vi phạm điều khoản dịch vụ (ToS / Spam / Scam)"

    # Python là nguồn DUY NHẤT cho B1 — strip bất kỳ B1 nào từ LLM
    signals = [s for s in analysis.detected_signals if s != "B1"]
    if budget_detected is not None and budget_detected >= 300:
        signals.append("B1")
        if "B5" in signals:
            signals.remove("B5")

    total_score, scoring_details = calculate_python_score(signals)

    extracted_signals = ExtractedSignals(
        role_type=analysis.role_type,
        specific_error=analysis.specific_error,
        tool=analysis.tool,
        tech_stack=analysis.tech_stack,
        request_summary=analysis.request_summary,
        needs_enrichment=total_score >= 50,
    )

    is_dq = dq_reason is not None
    return is_dq, dq_reason, total_score, scoring_details, extracted_signals