# app/core/fallback.py
import asyncio
import logging
import re
import time

from app.core.llm import get_model_chain, ModelEntry

logger = logging.getLogger(__name__)

_PROVIDER_EXHAUSTED_KEYWORDS = [
    "tokens per day", "(tpd)", "tokens per month",
    "resource_exhausted", "quota exceeded",
    "daily limit", "monthly limit",
]

_RATE_LIMIT_KEYWORDS = [
    "tokens per minute", "(tpm)", "rate limit",
    "too many requests", "service unavailable",
    "503", "502", "timeout", "timed out",
]

_OPENROUTER_GLOBAL_LIMIT_KEYWORDS = [
    "free-models-per-min",
    "free-models-per-day",
]


def is_provider_exhausted(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(k in text for k in _PROVIDER_EXHAUSTED_KEYWORDS)


def is_openrouter_global_rate_limited(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(k in text for k in _OPENROUTER_GLOBAL_LIMIT_KEYWORDS)


def is_rate_limited(exc: Exception) -> bool:
    if is_provider_exhausted(exc) or is_openrouter_global_rate_limited(exc):
        return False
    text = str(exc).lower()
    return "429" in text or any(k in text for k in _RATE_LIMIT_KEYWORDS)


def is_schema_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "output retries" in text or "exceeded maximum" in text


def _parse_reset_wait(exc: Exception) -> float:
    text = str(exc)
    match = re.search(r"['\"]X-RateLimit-Reset['\"]:\s*['\"]?(\d+)['\"]?", text)
    if match:
        wait = (int(match.group(1)) / 1000) - time.time()
        return max(wait + 0.5, 0)
    return 60.0


async def run_with_fallback(
    agent,
    prompt: str,
    retries_per_model: int = 2,
    deps=None,
):
    entries: list[ModelEntry] = get_model_chain()

    if not entries:
        raise RuntimeError("No LLM configured. Check API keys in .env.")

    exhausted_providers: set[str] = set()
    last_error: Exception | None = None
    global_rate_limit_wait_until: float = 0.0

    for entry in entries:
        model = entry.model
        model_name = entry.model_name
        provider_name = entry.provider_name  # ← dùng trực tiếp, không cần parse

        if provider_name in exhausted_providers:
            logger.warning(
                "Skipping %s — provider '%s' exhausted.",
                model_name, provider_name,
            )
            continue

        # Chờ nếu đang trong global rate limit window (OpenRouter)
        now = time.time()
        if global_rate_limit_wait_until > now:
            remaining = global_rate_limit_wait_until - now
            logger.info(
                "Global rate limit active. Waiting %.1fs before trying %s...",
                remaining, model_name,
            )
            await asyncio.sleep(remaining)

        logger.info("Trying model: %s (provider: %s)", model_name, provider_name)

        for attempt in range(retries_per_model + 1):
            try:
                kwargs: dict = {"model": model}
                if deps is not None:
                    kwargs["deps"] = deps

                result = await agent.run(prompt, **kwargs)
                logger.info("Success with model: %s", model_name)
                return result

            except Exception as exc:
                last_error = exc
                logger.warning(
                    "Model %s | attempt %s/%s | error: %s",
                    model_name, attempt + 1, retries_per_model + 1, exc,
                )

                if is_openrouter_global_rate_limited(exc):
                    wait_time = _parse_reset_wait(exc)
                    global_rate_limit_wait_until = time.time() + wait_time
                    logger.warning(
                        "OpenRouter global rate limit. Waiting %.1fs...", wait_time,
                    )
                    break

                elif is_provider_exhausted(exc):
                    exhausted_providers.add(provider_name)
                    logger.warning(
                        "Provider '%s' exhausted. Skipping all '%s' models.",
                        provider_name, provider_name,
                    )
                    break

                elif is_rate_limited(exc):
                    if attempt < retries_per_model:
                        wait_time = 2 ** attempt
                        logger.info(
                            "Rate limited on %s. Retrying in %ss...",
                            model_name, wait_time,
                        )
                        await asyncio.sleep(wait_time)

                elif is_schema_error(exc):
                    logger.warning("Schema error on %s. Switching to next model.", model_name)
                    break

                else:
                    raise  # Lỗi thật — raise ngay

        logger.warning("Moving to next model after %s failed.", model_name)

    raise RuntimeError(
        f"All models exhausted. Last error: {last_error}\n"
        f"Exhausted providers: {exhausted_providers}"
    )