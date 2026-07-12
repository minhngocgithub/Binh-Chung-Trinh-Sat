"""Weaver enrichment service v3."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Optional
from urllib.parse import urlparse

# ── Optional imports (graceful degradation) ──────────────────────────────
try:
    from bs4 import BeautifulSoup
    HAS_SOUP = True
except ImportError:
    BeautifulSoup = None
    HAS_SOUP = False

try:
    import redis.asyncio as aioredis
    HAS_REDIS = True
except ImportError:
    aioredis = None
    HAS_REDIS = False

try:
    from crawl4ai import AsyncWebCrawler
    HAS_CRAWL4AI = True
except ImportError:
    AsyncWebCrawler = None
    HAS_CRAWL4AI = False

logger = logging.getLogger("bcts.weaver")

# ── Constants ──────────────────────────────────────────────────────────────
MAX_DESC_LENGTH = 200
REQUEST_TIMEOUT = 15  # seconds
MAX_RETRIES = 2

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
REDIS_CACHE_TTL = int(os.getenv("REDIS_CACHE_TTL", "86400"))  # 24h

CRAWL4AI_TIMEOUT = 15_000  # ms

# ── 9router AI enrichment ────────────────────────────────────────────────
NINE_ROUTER_URL = os.getenv("NINE_ROUTER_URL", "http://9router:20128")
NINE_ROUTER_API_KEY = os.getenv("NINE_ROUTER_API_KEY", "")
NINE_ROUTER_MODEL = os.getenv("NINE_ROUTER_MODEL", "gpt-4o-mini")

# ── Regex patterns ─────────────────────────────────────────────────────────

URL_REGEX = re.compile(
    r"https?://(?:www\.)?[-a-zA-Z0-9@:%._+~#=]{1,256}\.[a-zA-Z0-9()]{1,6}\b[-a-zA-Z0-9()@:%_+.~#?&/=]*"
)
DOMAIN_REGEX = re.compile(
    r"(?:https?://)?(?:www\.)?([a-zA-Z0-9][-a-zA-Z0-9]*\.[a-zA-Z]{2,})(?:[/\s\"']|$)"
)

# ── Company name extraction ──────────────────────────────────────────────

# Generic words that are NOT valid company names
_STOP_WORDS = {
    "applicants", "applicant", "candidate", "candidates",
    "must", "should", "please", "required", "requirements",
    "qualifications", "experience", "skills", "description",
    "overview", "about", "summary", "introduction", "welcome",
    "we", "our", "your", "their", "this", "that",
    "new", "free", "best", "top", "great", "excellent",
    "looking", "hiring", "seeking", "wanted", "need",
    "just", "also", "very", "many", "some", "each",
    "job", "role", "position", "remote", "based", "located", "location",
}


def _is_valid_company_name(name: str) -> bool:
    """Validate whether extracted text is likely a real company name."""
    name = name.strip()
    if len(name) < 3:
        return False
    if not name[0].isupper():
        return False
    if sum(c.isalpha() for c in name) < len(name) * 0.5:
        return False
    words = name.split()
    if len(words) > 6:
        return False
    first_word = words[0].lower().strip(",.!?;:")
    if first_word in _STOP_WORDS:
        return False
    verb_indicators = {"must", "should", "will", "can", "may", "need", "based", "located"}
    if any(w.lower() in verb_indicators for w in words[:2]):
        return False
    return True


HIRING_PATTERNS = [
    re.compile(
        r"(?:we'?re|we are)\s+(?:hiring|looking for|seeking)\s+(?:a|an|the|)"
        r"\s*(?:\w+\s+){0,4}(?:at|for|@)\s+"
        r"([A-Z][A-Za-z0-9\s&.]+?)(?:\s*[,.!?]|\s*$)", re.I
    ),
    re.compile(
        r"([A-Z][A-Za-z0-9\s&.]+?)\s+(?:is|are)\s+(?:hiring|looking for|seeking)", re.I
    ),
    re.compile(
        r"(?:join|work at|work for)\s+([A-Z][A-Za-z0-9\s&.]+?)(?:\s*[,.!?]|\s*$)", re.I
    ),
]

STARTUP_PATTERNS = [
    re.compile(
        r"(?:my startup|my company|I founded|I built|I run|we built|we run)"
        r"\s+(?:a|an|the|)\s*(?:\w+\s+){0,4}(?:called|named|)\s+"
        r"([A-Z][A-Za-z0-9\s&.]+?)(?:\s*[,.!?]|\s*$)", re.I
    ),
    re.compile(
        r"(?:building|launching|working on)\s+(?:a|an|the|)\s*(?:\w+\s+){0,4}"
        r"(?:called|named|)\s+([A-Z][A-Za-z0-9\s&.]+?)(?:\s*[,.!?]|\s*$)", re.I
    ),
]

FORUM_PATTERNS = [
    re.compile(
        r"(?:I work at|I'm at|I am at)\s+([A-Z][A-Za-z0-9\s&.]+?)(?:\s*[,.!?]|\s*$)", re.I
    ),
    re.compile(
        r"(?:CEO|CTO|founder|co-founder|engineer|employee)\s+(?:at|of|@)\s+"
        r"([A-Z][A-Za-z0-9\s&.]+?)(?:\s*[,.!?]|\s*$)", re.I
    ),
]

LEAD_PATTERNS = [
    re.compile(
        r"(?:hi|hello|hey)\s+(?:guys|team|everyone|all)[,.]?\s+"
        r"(?:my name['']s\s+\w+\s+(?:from|at|of)\s+)?"
        r"([A-Z][A-Za-z0-9\s&.]+?)(?:\s*[,.!?]|\s*$)", re.I
    ),
]

SKIP_DOMAINS = {
    "discord.com", "discord.gg", "reddit.com", "twitter.com", "x.com",
    "hackernews.com", "news.ycombinator.com", "linkedin.com", "facebook.com",
    "instagram.com", "youtube.com", "tiktok.com", "pinterest.com",
    "medium.com", "dev.to", "github.com", "gitlab.com",
}


# ── Redis helpers ──────────────────────────────────────────────────────────

async def get_redis() -> aioredis.Redis | None:
    """Get or create Redis connection."""
    if not HAS_REDIS:
        return None
    try:
        r = aioredis.from_url(REDIS_URL, decode_responses=True, socket_connect_timeout=2)
        await r.ping()
        return r
    except Exception:
        logger.warning("Redis unavailable — running without cache")
        return None


async def cache_get(r: aioredis.Redis | None, key: str) -> Optional[dict]:
    if r is None:
        return None
    try:
        data = await r.get(f"weaver:v3:{key}")
        if data:
            return json.loads(data)
    except Exception:
        pass
    return None


async def cache_set(r: aioredis.Redis | None, key: str, value: dict, ttl: int = REDIS_CACHE_TTL):
    if r is None:
        return
    try:
        await r.setex(f"weaver:v3:{key}", ttl, json.dumps(value, default=str))
    except Exception:
        pass


# ── URL / Domain extraction ────────────────────────────────────────────────

def extract_domain_from_text(text: str) -> Optional[str]:
    """Extract a company domain from free text."""
    urls = URL_REGEX.findall(text)
    if urls:
        for url in urls:
            parsed = urlparse(url)
            domain = (parsed.netloc or parsed.path).lower()
            domain = re.sub(r"^www\.", "", domain)
            if domain and domain not in SKIP_DOMAINS:
                return domain
    domains = DOMAIN_REGEX.findall(text)
    if domains:
        for d in domains:
            d = d.lower()
            if d not in SKIP_DOMAINS:
                return d
    return None


def extract_company_name_from_text(text: str) -> Optional[str]:
    """Extract company name from lead content using heuristic patterns."""
    patterns = HIRING_PATTERNS + STARTUP_PATTERNS + FORUM_PATTERNS + LEAD_PATTERNS
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            name = match.group(1).strip()
            name = re.sub(r"\s+and\s+$", "", name)
            name = re.sub(r"\s*\(.*?\)\s*$", "", name)
            name = name.strip()
            if _is_valid_company_name(name):
                return name
    return None


# ── Description helpers ────────────────────────────────────────────────────

def truncate(text: str, maxlen: int = MAX_DESC_LENGTH) -> str:
    """Truncate text to maxlen chars, preferring sentence boundaries."""
    if len(text) <= maxlen:
        return text
    truncated = text[:maxlen]
    last_period = truncated.rfind(".")
    if last_period > maxlen * 0.5:
        return text[: last_period + 1]
    return truncated.strip()


# ── Crawl4AI homepage crawling ─────────────────────────────────────────────

async def crawl_homepage(domain: str) -> Optional[dict]:
    """
    Crawl company homepage using Crawl4AI.
    Falls back to httpx + BeautifulSoup if Crawl4AI is unavailable.
    Returns dict with title, description, or None on failure.
    """
    url = f"https://{domain}"
    attempt = 0
    last_error = None

    if HAS_CRAWL4AI:
        while attempt <= MAX_RETRIES:
            attempt += 1
            try:
                async with AsyncWebCrawler() as crawler:
                    result = await crawler.arun(
                        url=url,
                        word_count_threshold=10,
                        extraction_strategy="NoExtractionStrategy",
                        bypass_cache=True,
                        verbose=False,
                        timeout=CRAWL4AI_TIMEOUT,
                    )

                if not result.success:
                    logger.warning("Crawl4AI failed for %s (attempt %d): %s", url, attempt, result.error_message)
                    last_error = result.error_message
                    continue

                # Extract metadata from HTML
                soup = None
                if HAS_SOUP and result.html:
                    soup = BeautifulSoup(result.html, "html.parser")

                title = None
                meta_description = None
                og_description = None
                json_ld = None

                if soup:
                    if soup.title and soup.title.string:
                        title = soup.title.string.strip()
                    meta_tag = soup.find("meta", attrs={"name": "description"})
                    if meta_tag and meta_tag.get("content"):
                        meta_description = meta_tag["content"].strip()
                    og_tag = soup.find("meta", attrs={"property": "og:description"})
                    if og_tag and og_tag.get("content"):
                        og_description = og_tag["content"].strip()
                    for script in soup.find_all("script", type="application/ld+json"):
                        try:
                            data = json.loads(script.string)
                            json_ld = data
                            break
                        except Exception:
                            continue

                description = (
                    og_description or meta_description
                    or (result.markdown[:MAX_DESC_LENGTH] if result.markdown else None)
                )
                if description:
                    description = truncate(description)

                return {
                    "title": title,
                    "meta_description": meta_description,
                    "og_description": og_description,
                    "json_ld": json_ld,
                    "description": description,
                    "markdown_snippet": result.markdown[:300] if result.markdown else None,
                }

            except asyncio.TimeoutError:
                last_error = "timeout"
                logger.warning("Timeout crawling %s (attempt %d)", url, attempt)
            except Exception as exc:
                last_error = str(exc)
                logger.warning("Error crawling %s (attempt %d): %s", url, attempt, exc)

            if attempt <= MAX_RETRIES:
                await asyncio.sleep(1 * attempt)
    else:
        # ── Fallback: httpx + BeautifulSoup ──
        try:
            import httpx
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
                resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
                resp.raise_for_status()

            if HAS_SOUP:
                soup = BeautifulSoup(resp.text, "html.parser")
                title = soup.title.string.strip() if soup.title and soup.title.string else None
                meta_tag = soup.find("meta", attrs={"name": "description"})
                meta_description = meta_tag["content"].strip() if meta_tag and meta_tag.get("content") else None
                og_tag = soup.find("meta", attrs={"property": "og:description"})
                og_description = og_tag["content"].strip() if og_tag and og_tag.get("content") else None
                description = truncate(og_description or meta_description or resp.text[:MAX_DESC_LENGTH])
            else:
                description = truncate(resp.text[:MAX_DESC_LENGTH])

            return {"title": title if HAS_SOUP else None, "description": description}
        except Exception as exc:
            last_error = str(exc)
            logger.warning("Fallback crawl failed for %s: %s", url, exc)

    logger.warning("All crawl attempts failed for %s: %s", url, last_error)
    return None


# ── 9router AI enrichment ─────────────────────────────────────────────────

async def enrich_via_9router(content: str, domain: Optional[str] = None) -> Optional[dict]:
    """
    Use 9router (OpenAI-compatible proxy) to extract company info from lead content.
    Returns dict with company_name, company_description, or None on failure.
    """
    if not NINE_ROUTER_API_KEY:
        logger.debug("9router: no API key configured, skipping")
        return None

    system_prompt = (
        "You are a data extraction assistant. "
        "Extract the company name and a concise company description (max 200 chars) "
        "from the following lead content. "
        "If no company info is found, return null values.\n\n"
        "Respond ONLY with valid JSON: "
        '{"company_name": "...", "company_description": "..."}'
    )

    user_content = f"Domain: {domain or 'unknown'}\n\nContent:\n{content[:2000]}"

    try:
        import httpx
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{NINE_ROUTER_URL}/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {NINE_ROUTER_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": NINE_ROUTER_MODEL,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_content},
                    ],
                    "temperature": 0.1,
                    "max_tokens": 300,
                },
            )
            resp.raise_for_status()
            data = resp.json()

        ai_text = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        if not ai_text:
            logger.warning("9router: empty AI response")
            return None

        # Clean markdown code fences
        ai_text = re.sub(r"^```(?:json)?\s*", "", ai_text.strip())
        ai_text = re.sub(r"\s*```$", "", ai_text)

        result = json.loads(ai_text)
        company_name = result.get("company_name") or None
        company_description = result.get("company_description") or None

        if company_description:
            company_description = truncate(company_description)

        logger.info("9router: extracted company_name=%s desc_length=%s",
                     company_name, len(company_description) if company_description else 0)
        return {
            "company_name": company_name,
            "company_description": company_description,
        }

    except ImportError:
        logger.debug("9router: httpx not available, skipping")
        return None
    except Exception as exc:
        logger.warning("9router enrichment failed: %s", exc)
        return None


# ── Main enrichment pipeline ───────────────────────────────────────────────

async def enrich_lead(url: Optional[str], content: str) -> dict:
    """
    Enrich a lead and return the enriched_data dict.

    1. Extract URL/domain from content if url is null.
    2. Extract company name from content patterns.
    3. If domain found → crawl homepage via Crawl4AI (with Redis cache).
    4. If crawl fails → try 9router AI enrichment.
    5. If 9router fails → generate description from content.
    6. Return dict with company_domain, company_description, weaver_available, error.
    """
    content = content or ""
    url = url or ""

    # ── Step 1: Determine target domain ──
    domain = None
    if url and url.strip():
        parsed = urlparse(url)
        domain = (parsed.netloc or parsed.path).lower()
        domain = re.sub(r"^www\.", "", domain)
        if not re.match(r"^[a-zA-Z0-9][-a-zA-Z0-9]*\.[a-zA-Z]{2,}$", domain or ""):
            domain = None
    else:
        domain = extract_domain_from_text(content)

    if domain and domain in SKIP_DOMAINS:
        domain = None

    # ── Step 2: Extract company name (regex baseline) ──
    # Note: company_name from regex is NOT used for final output —
    # only website crawl or 9router AI can confirm the name.
    _regex_name = extract_company_name_from_text(content)

    # ── Step 3: Attempt homepage crawl ──
    crawl_data = None
    error: Optional[str] = None
    company_description: Optional[str] = None
    company_name: Optional[str] = None

    if domain:
        redis = await get_redis()
        cache_key = f"crawl:{domain}"
        cached = await cache_get(redis, cache_key)

        if cached:
            crawl_data = cached
            logger.info("Cache hit for domain=%s", domain)
        else:
            crawl_data = await crawl_homepage(domain)
            if crawl_data:
                await cache_set(redis, cache_key, crawl_data)

        if crawl_data and crawl_data.get("description"):
            company_description = crawl_data["description"]
        elif crawl_data and crawl_data.get("markdown_snippet"):
            company_description = truncate(crawl_data["markdown_snippet"])

    # ── Step 4: Fallback — try 9router AI enrichment ──
    if not company_description and content:
        logger.info("Crawl failed or no description — trying 9router AI enrichment")
        ai_result = await enrich_via_9router(content, domain)
        if ai_result:
            if ai_result.get("company_name"):
                company_name = ai_result["company_name"]
            if ai_result.get("company_description"):
                company_description = ai_result["company_description"]
                logger.info("9router enrichment successful for domain=%s", domain)

    # ── Step 5: No reliable data → return null ──
    # PRD: if website cannot be crawled, return null, never hallucinate
    if not company_description:
        return {
            "company_domain": None,
            "company_description": None,
            "company_name": None,
            "weaver_available": False,
            "error": "Unable to confidently identify company",
        }

    return {
        "company_domain": domain,
        "company_description": company_description,
        "company_name": company_name,
        "weaver_available": True,
        "error": None,
    }
