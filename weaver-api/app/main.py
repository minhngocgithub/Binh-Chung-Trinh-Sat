"""
Binh Chủng Trinh Sát — Weaver API v3
Enrichment service: appends enriched_data to the original lead payload.
Endpoints: POST /weaver, GET /health
"""

from __future__ import annotations

import logging
from copy import deepcopy

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.services import enrich_lead

logging.basicConfig(
    level=logging.INFO,
    format='{"time":"%(asctime)s","name":"%(name)s","level":"%(levelname)s","message":"%(message)s"}',
)
logger = logging.getLogger("bcts.weaver")

app = FastAPI(
    title="Binh Chủng Trinh Sát — Weaver API",
    description="Enrichment service — appends enriched_data to original lead payload",
    version="3.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/weaver")
async def post_weaver(request: Request):
    """
    Enrich a lead and return the COMPLETE original payload + enriched_data.

    NEVER drops or overwrites original lead fields.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}

    # ── Preserve original payload ──
    response = deepcopy(body)

    # ── Extract lead info for enrichment ──
    lead = body.get("lead", {})
    if not isinstance(lead, dict):
        lead = {}

    # Support both nested (lead.url) and flat (body.url) input
    content = lead.get("content", "") or body.get("content", "") or ""
    url = lead.get("url") or body.get("url")

    logger.info(
        "POST /weaver lead_id=%s source=%s url=%s",
        lead.get("lead_id"), lead.get("source"), url,
    )

    # ── Run enrichment ──
    enriched = await enrich_lead(url, content)

    # ── Append enriched_data to the preserved payload ──
    response["enriched_data"] = enriched

    return response


@app.get("/health")
async def health():
    return {"status": "ok", "service": "weaver-api", "version": "3.1.0"}
