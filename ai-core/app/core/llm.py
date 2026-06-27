# app/core/llm.py
from dataclasses import dataclass
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from app.config import settings


@dataclass
class ModelEntry:
    """Wrapper giữ model + tên provider thật để fallback.py dùng."""
    model: OpenAIChatModel
    provider_name: str  # "groq" | "google" | "deepseek" | "openrouter"

    @property
    def model_name(self) -> str:
        return getattr(self.model, "model_name", "unknown")


def _make_groq(model_name: str) -> ModelEntry:
    return ModelEntry(
        model=OpenAIChatModel(
            model_name,
            provider=OpenAIProvider(
                api_key=settings.groq_api_key,
                base_url="https://api.groq.com/openai/v1",
            ),
        ),
        provider_name="groq",
    )


def _make_google(model_name: str) -> ModelEntry:
    return ModelEntry(
        model=OpenAIChatModel(
            model_name,
            provider=OpenAIProvider(
                api_key=settings.google_api_key,
                base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
            ),
        ),
        provider_name="google",
    )


def _make_deepseek(model_name: str) -> ModelEntry:
    return ModelEntry(
        model=OpenAIChatModel(
            model_name,
            provider=OpenAIProvider(
                api_key=settings.deepseek_api_key,
                base_url="https://api.deepseek.com/v1",
            ),
        ),
        provider_name="deepseek",
    )


def _make_openrouter(model_name: str) -> ModelEntry:
    return ModelEntry(
        model=OpenAIChatModel(
            model_name,
            provider=OpenAIProvider(
                api_key=settings.openrouter_api_key,
                base_url="https://openrouter.ai/api/v1",
            ),
        ),
        provider_name="openrouter",
    )


def get_model_chain() -> list[ModelEntry]:
    chain: list[ModelEntry] = []

    # Primary — Groq: 30 req/min, 14,400 req/day, ~1-2s response
    if settings.groq_api_key:
        chain.append(_make_groq("llama-3.3-70b-versatile"))
        chain.append(_make_groq("gemma2-9b-it"))

    # Fallback 1 — Google AI Studio: quota riêng
    if settings.google_api_key:
        chain.append(_make_google("gemini-2.0-flash"))

    # Fallback 2 — DeepSeek: free credits
    if settings.deepseek_api_key:
        chain.append(_make_deepseek("deepseek-chat"))

    # Backup cuối — OpenRouter
    if settings.openrouter_api_key:
        chain.append(_make_openrouter("meta-llama/llama-3.3-70b-instruct:free"))

    return chain


def get_primary_model() -> OpenAIChatModel:
    """
    Model đầu tiên available — dùng để khởi tạo Agent tĩnh.
    run_with_fallback() sẽ override khi gọi thật.
    """
    chain = get_model_chain()
    if chain:
        return chain[0].model
    raise RuntimeError("No LLM configured. Check API keys in .env.")