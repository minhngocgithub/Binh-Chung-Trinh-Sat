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


def _make_mistral(model_name: str) -> ModelEntry:
    return ModelEntry(
        model=OpenAIChatModel(
            model_name,
            provider=OpenAIProvider(
                api_key=settings.mistral_api_key,
                base_url="https://api.mistral.ai/v1",
            ),
        ),
        provider_name="mistral",
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

    if settings.groq_api_key:
        chain.append(_make_groq("openai/gpt-oss-120b"))
        chain.append(_make_groq("openai/gpt-oss-20b"))

    if settings.google_api_key:
        chain.append(_make_google("gemini-2.5-flash"))
        chain.append(_make_google("gemini-2.5-flash-lite"))

    if settings.mistral_api_key:
        chain.append(_make_mistral("mistral-large"))

    if settings.openrouter_api_key:
        chain.append(_make_openrouter("openai/gpt-oss-120b:free"))
        chain.append(_make_openrouter("google/gemma-4-31b-it:free"))

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