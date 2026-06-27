# app/core/writer.py
import re
from dataclasses import dataclass
from pydantic_ai import Agent, RunContext
from pydantic_ai.models.openai import OpenAIChatModel

from app.core.fallback import run_with_fallback
from app.core.llm import get_primary_model
from app.schemas.api_models import DraftInput, DraftResponse, VariablesUsed, ValidationStatus


# ---------------------------------------------------------------------------
# 1. Dependency context truyền vào system prompt động
# ---------------------------------------------------------------------------

@dataclass
class WriterDeps:
    name: str
    role_type: str
    channel: str


# ---------------------------------------------------------------------------
# 2. Các hằng số phân loại source
# ---------------------------------------------------------------------------

_HN_EMAIL_WORD_LIMIT = 200
_DEFAULT_WORD_LIMIT = 150

_HN_EMAIL_SOURCES: frozenset[str] = frozenset({"hackernews", "hacker_news", "hn", "email"})

_INVALID_AUTHOR_VALUES: frozenset[str] = frozenset(
    {"none", "null", "n/a", "anonymous", "unknown", "hey", "user", ""}
)

# Dòng "Subject:" ở đầu draft — KHÔNG tính vào word_count (BA confirm)
_SUBJECT_LINE_PATTERN = re.compile(r"(?i)^\s*subject\s*:.*\n?")


# ---------------------------------------------------------------------------
# 3. System Prompt — inject động qua RunContext
# ---------------------------------------------------------------------------

WRITER_SYSTEM_PROMPT = """
You are a Senior AI Solutions Architect writing a cold outreach message on behalf of SPORTAIV.
You are writing directly to: {name} (Role: {role_type}).
Channel source: {channel}.

============================================================
CRITICAL OUTREACH PRINCIPLES (NEVER VIOLATE)
============================================================
1. PEER-TO-PEER TONE
   Write as a fellow engineer or product architect who ran into the same problem.
   Never use sales clichés:
   - BAD: "I hope this email finds you well"
   - BAD: "We are a leading AI agency"
   - BAD: "I would love to introduce our services"
   - GOOD: "Saw your post — that 500 on the Stripe webhook is almost always a payload mismatch."

2. GIVE VALUE FIRST (CTA rules)
   Your Call-To-Action must NOT invite them to a call, Zoom, or calendar booking.
   Instead, offer ONE free concrete asset that helps them fix the problem NOW:
   - n8n / Make / Zapier   → share a pre-built JSON workflow export / blueprint
   - Custom Python / APIs  → share a script snippet or architecture diagram
   - LangGraph / CrewAI    → share multi-agent state code or a 2-min Loom walkthrough

3. FALLBACK RULES (STRICTLY FOLLOW)
   - If name is missing or generic → open with "Hey there" or "Hi". NEVER use brackets.
   - If company_description is "No website context provided." → do NOT mention the company's
     industry, domain, products, or revenue. Focus only on the technical issue in their post.

4. ZERO PLACEHOLDERS IN OUTPUT
   Do NOT include any bracketed text like [My Name], [Company], [Link], [INSERT HERE].
   Do NOT sign off at all — no "Best,", "Cheers,", your name, "SPORTAIV", or any team/
   company identity. A real engineer DMing a peer about a shared bug doesn't sign their
   message. End directly after your last sentence (the offer or question).

============================================================
AUDIENCE LANGUAGE RULES (based on Receiver Role)
============================================================
- role_type = "TECH" (Engineer, Developer, DevOps, Architect):
  * Use precise technical jargon: "HTTP 500", "webhook payload", "retry backoff", "state graph"
  * Assume they understand the stack — no need to explain basics.

- role_type = "NON_TECH" (Founder, CEO, PM, Marketing, Ops):
  * Translate everything to business impact. Zero jargon.
  * BAD: "your n8n HTTP node is hitting a 500 on the Stripe webhook endpoint"
  * GOOD: "your payment sync is breaking — new orders aren't updating automatically"
  * Forbidden words for NON_TECH: webhook, HTTP 500, JSON payload, endpoint, API call,
    node, pipeline, cron, async, retry, state machine.

============================================================
CHANNEL-SPECIFIC FORMAT (HARD RULES)
============================================================
Discord / Slack DM:
  * MAXIMUM 150 words — count carefully before finalising.
  * No Subject line.
  * Conversational, direct. Jump straight into their error or bottleneck.

Hacker News / Email:
  * MAXIMUM 200 words for the BODY only — count carefully before finalising.
    The "Subject:" line itself does NOT count toward this limit.
  * MUST include an engineering-focused "Subject:" line at the very top.
  * Structure: 4 to 6 short paragraphs.

Developer Forums / Reddit / Comments:
  * MAXIMUM 150 words — count carefully before finalising.
  * No Subject line.
  * Lead with step-by-step technical approach (Step 1, Step 2, ...) before any soft offer to DM.
"""


# ---------------------------------------------------------------------------
# 4. Khởi tạo Writer Agent — dùng get_primary_model() thay vì hardcode provider
#    run_with_fallback() sẽ override model khi gọi thật
# ---------------------------------------------------------------------------

writer_agent = Agent(
    model=get_primary_model(),
    deps_type=WriterDeps,
)


@writer_agent.system_prompt
def dynamic_system_prompt(ctx: RunContext[WriterDeps]) -> str:
    return WRITER_SYSTEM_PROMPT.format(
        name=ctx.deps.name,
        role_type=ctx.deps.role_type,
        channel=ctx.deps.channel,
    )


# ---------------------------------------------------------------------------
# 5. Validate đầu ra bằng Python thuần
# ---------------------------------------------------------------------------

_PLACEHOLDER_PATTERN = re.compile(r"\[.+?\]")


def validate_outreach_draft(draft_text: str, source: str) -> tuple[int, bool, bool]:
    """
    Trả về (word_count, has_placeholder, word_count_ok).

    BA confirm (2026-06): với HN/Email, dòng "Subject:" KHÔNG tính vào word_count.
    has_placeholder check trên toàn bộ draft kể cả Subject.
    """
    has_placeholder = bool(_PLACEHOLDER_PATTERN.search(draft_text))

    source_key = source.strip().lower()
    is_hn_email = source_key in _HN_EMAIL_SOURCES

    body_text = (
        _SUBJECT_LINE_PATTERN.sub("", draft_text, count=1) if is_hn_email else draft_text
    )
    word_count = len(body_text.split())

    limit = _HN_EMAIL_WORD_LIMIT if is_hn_email else _DEFAULT_WORD_LIMIT
    word_count_ok = word_count <= limit

    return word_count, has_placeholder, word_count_ok


def _get_word_limit(source: str) -> int:
    return _HN_EMAIL_WORD_LIMIT if source.strip().lower() in _HN_EMAIL_SOURCES else _DEFAULT_WORD_LIMIT


# ---------------------------------------------------------------------------
# 6. Hàm điều phối chính
# ---------------------------------------------------------------------------

async def run_writer_agent(input_data: DraftInput) -> DraftResponse:
    # --- Fallback source ---
    effective_source: str = input_data.source or getattr(input_data.lead, "source", "forum") or "forum"

    # --- Làm sạch tên người nhận ---
    # Strip platform username prefixes: /u/ (Reddit), u/, @ trước khi check invalid
    raw_name = (input_data.lead.author or "").strip()
    for prefix in ("/u/", "u/", "@"):
        if raw_name.lower().startswith(prefix):
            raw_name = raw_name[len(prefix):]
            break

    clean_name = (
        raw_name
        if raw_name.lower() not in _INVALID_AUTHOR_VALUES
        else "Hey there"
    )

    # --- Fallback thông tin website ---
    # Chỉ dùng nếu có company_domain thật — tránh Weaver hallucinate
    website_desc: str | None = None
    if (
        input_data.enriched_data
        and input_data.enriched_data.company_description
        and input_data.enriched_data.company_domain
    ):
        website_desc = input_data.enriched_data.company_description

    # --- Xác định intent type để Writer chọn đúng framing ---
    # A3 (hiring post): pitch capability; A1/A2 (bug/question): offer fix
    _hiring_keywords = (
        "looking for", "hiring", "seeking freelancer", "need a developer",
        "need someone", "we are looking", "paid gig", "compensation",
    )
    is_hiring_post = any(
        kw in (input_data.lead.content or "").lower() for kw in _hiring_keywords
    )
    intent_context = (
        "POST TYPE: Hiring/Job post — they are LOOKING TO HIRE someone with this tech stack. "
        "Do NOT offer to fix a bug. Do NOT say 'let me help with your project'. "
        "Instead: briefly reference a similar system you have shipped, then offer "
        "one concrete asset (architecture diagram, workflow export, or short Loom) "
        "that shows your capability for this exact scope."
        if is_hiring_post
        else "POST TYPE: Technical help request — offer a direct fix for their specific issue."
    )

    # --- User prompt ---
    # Dùng request_summary thay vì content thô để tránh LLM bị nhiễu bởi noise
    stated_issue = (
        input_data.extracted_signals.specific_error
        or input_data.extracted_signals.request_summary
        or (input_data.lead.content or "")[:300]  # fallback cuối: 300 ký tự đầu
    )

    prompt_context = (
        f"Receiver Name: {clean_name}\n"
        f"Receiver Role: {input_data.extracted_signals.role_type}\n"
        f"{intent_context}\n"
        f"Stated Issue / Request: {stated_issue}\n"
        f"Tool Mentioned: {input_data.extracted_signals.tool or 'AI Automation'}\n"
        f"Tech Stack: {', '.join(input_data.extracted_signals.tech_stack)}\n"
        f"Request Summary: {input_data.extracted_signals.request_summary or 'Technical assistance'}\n"
        f"Company/Website Context: {website_desc or 'No website context provided.'}\n"
    )

    deps = WriterDeps(
        name=clean_name,
        role_type=input_data.extracted_signals.role_type,
        channel=effective_source,
    )

    # --- Gọi LLM qua fallback chain ---
    try:
        result = await run_with_fallback(writer_agent, prompt_context, retries_per_model=1, deps=deps)
        raw_draft: str = result.output
    except Exception as e:
        raise RuntimeError(f"Writer Agent thất bại: {e}") from e

    # --- Kiểm duyệt chất lượng ---
    word_count, has_placeholder, word_count_ok = validate_outreach_draft(
        raw_draft, effective_source
    )

    if has_placeholder:
        raise ValueError(
            f"Draft còn sót placeholder — không gửi được. "
            f"Preview: {raw_draft[:120]}..."
        )

    if not word_count_ok:
        limit = _get_word_limit(effective_source)
        raise ValueError(
            f"Draft vượt giới hạn từ ({word_count}/{limit} từ) — "
            f"LLM cần viết ngắn hơn."
        )

    return DraftResponse(
        lead_id=input_data.lead_id,
        recipient_role_type=input_data.extracted_signals.role_type,
        channel_source=effective_source,
        generated_draft=raw_draft,
        word_count=word_count,
        variables_used=VariablesUsed(
            name=clean_name,
            issue=input_data.extracted_signals.specific_error,
            tool=input_data.extracted_signals.tool,
            website_description=website_desc,
        ),
        validation=ValidationStatus(
            has_placeholder=has_placeholder,
            word_count_ok=word_count_ok,
        ),
    )