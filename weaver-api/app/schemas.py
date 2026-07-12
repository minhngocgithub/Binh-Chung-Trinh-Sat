"""Pydantic schemas for Weaver API input/output."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class LeadObject(BaseModel):
    """The original lead object — full passthrough."""
    lead_id: Optional[str] = None
    source: Optional[str] = None
    content: Optional[str] = None
    title: Optional[str] = None
    author: Optional[str] = None
    url: Optional[str] = None
    created_at: Optional[str] = None
    keywords_matched: Optional[list] = None
    type: Optional[str] = None
    raw_data: Optional[dict] = None


class EnrichedData(BaseModel):
    """Enrichment results appended to the original payload."""
    company_domain: Optional[str] = Field(default=None, description="Extracted company domain")
    company_description: Optional[str] = Field(default=None, description="Company description (max 200 chars)")
    company_name: Optional[str] = Field(default=None, description="Extracted company name")
    weaver_available: bool = False
    error: Optional[str] = None


class WeaverEnvelope(BaseModel):
    """Complete response preserving original lead + enrichment."""
    lead_id: Optional[str] = None
    total_score: Optional[int] = None
    lead: Optional[LeadObject] = None
    enriched_data: Optional[EnrichedData] = None
    # Allow any extra fields from the original payload to pass through
    class Config:
        extra = "allow"
