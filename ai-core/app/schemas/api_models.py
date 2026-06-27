# app/schemas/api_models.py
from pydantic import BaseModel
from typing import Optional, List, Literal

# ===== INPUT từ Dev1 (POST /score) =====
class LeadInput(BaseModel):
    lead_id: str
    source: str  # discord, hackernews, forum...
    content: str
    title: Optional[str] = None
    author: Optional[str] = None
    url: Optional[str] = None
    created_at: Optional[str] = None
    keywords_matched: List[str] = []
    intent_matched: List[str] = []    # pre-filter từ Dev1, echo lại để audit
    forum_labels: List[str] = []      # label lỗi từ Dev1 (memory-overflow, etc.)
    type: Optional[str] = None

# ===== OUTPUT DQ check (nội bộ, dùng trước khi gọi LLM) =====
class DQResult(BaseModel):
    is_disqualified: bool
    disqualification_reason: Optional[str] = None
    is_vip_whale: bool = False
    detected_budget: Optional[int] = None

# ===== OUTPUT /score (trả về Dev1) =====
class SignalDetail(BaseModel):
    signals_detected: List[str]
    score: int
    reason: str

# ===== OUTPUT từ LLM (nội bộ, dùng để enrich data) =====
class ExtractedSignals(BaseModel):
    role_type: Literal["TECH", "NON_TECH"]
    specific_error: Optional[str] = None
    tool: Optional[str] = None
    tech_stack: List[str] = []
    request_summary: Optional[str] = None
    needs_enrichment: bool = False

class ScoreResponse(BaseModel):
    # --- AI scoring result ---
    lead_id: str
    status: Literal["scored", "disqualified"]
    is_disqualified: bool
    disqualification_reason: Optional[str] = None
    is_vip_whale: bool = False
    total_score: Optional[int] = None
    classification: Optional[Literal["HOT", "WARM", "COLD", "DISCARD"]] = None
    scoring_details: Optional[dict[str, SignalDetail]] = None
    extracted_signals: Optional[ExtractedSignals] = None
    # --- Raw lead data echo lại để Dev1 không cần lưu riêng ---
    source: str
    title: Optional[str] = None
    content: str
    url: Optional[str] = None          # link bài gốc để Dev1 điều hướng outreach
    author: Optional[str] = None
    created_at: Optional[str] = None   # timestamp gốc để Dev1 sort/filter
    intent_matched: List[str] = []     # pre-filter signal từ Dev1, giữ lại để audit
    forum_labels: List[str] = []       # label lỗi từ Dev1 (memory-overflow, etc.)

# ===== INPUT cho POST /draft =====
class DraftLeadDetail(BaseModel):
    author: Optional[str] = None
    url: Optional[str] = None
    created_at: Optional[str] = None
    title: Optional[str] = None
    content: str

class EnrichedData(BaseModel):
    company_name: Optional[str] = None        # từ Weaver, cần validate vì dễ hallucinate
    company_domain: Optional[str] = None
    company_description: Optional[str] = None
    weaver_available: Optional[bool] = None
    error: Optional[str] = None

class DraftInput(BaseModel):
    lead_id: str
    source: Optional[str] = None  # Optional, fallback tu lead.source neu Dev1 quen gui
    type: Optional[str] = None
    classification: str           # bat buoc, lay tu /score response
    score: int
    extracted_signals: ExtractedSignals  # bat buoc, lay tu /score response
    lead: DraftLeadDetail
    enriched_data: Optional[EnrichedData] = None


# ===== OUTPUT cho POST /draft =====
class VariablesUsed(BaseModel):
    name: str
    issue: Optional[str] = None
    tool: Optional[str] = None
    website_description: Optional[str] = None

class ValidationStatus(BaseModel):
    has_placeholder: bool
    word_count_ok: bool

class DraftResponse(BaseModel):
    lead_id: str
    recipient_role_type: str
    channel_source: str
    generated_draft: str
    word_count: int
    variables_used: VariablesUsed
    validation: ValidationStatus