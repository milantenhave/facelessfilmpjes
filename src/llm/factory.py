"""Build the configured LLM provider, falling back to TemplateProvider."""
from __future__ import annotations

from ..utils.logger import get_logger
from .base import LLMProvider
from .template_provider import TemplateProvider

log = get_logger(__name__)


def build_provider(cfg: dict) -> LLMProvider:
    llm_cfg = cfg.get("llm", {})
    provider = (llm_cfg.get("provider") or "template").lower()
    kwargs = {
        "temperature": llm_cfg.get("temperature", 0.9),
        "max_tokens": llm_cfg.get("max_tokens", 800),
    }

    try:
        if provider == "openai":
            from .openai_provider import OpenAIProvider
            return OpenAIProvider(**kwargs)
        if provider == "anthropic":
            from .anthropic_provider import AnthropicProvider
            return AnthropicProvider(**kwargs)
        if provider == "ollama":
            from .ollama_provider import OllamaProvider
            return OllamaProvider(**kwargs)
    except Exception as exc:   # noqa: BLE001
        log.warning("LLM provider %s unavailable (%s). Falling back to template.",
                    provider, exc)

    return TemplateProvider()
