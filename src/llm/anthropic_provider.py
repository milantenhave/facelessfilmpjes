"""Claude / Anthropic provider."""
from __future__ import annotations

import json
import os

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from .base import LLMProvider, LLMResponse


class AnthropicProvider(LLMProvider):
    name = "anthropic"
    API = "https://api.anthropic.com/v1/messages"
    VERSION = "2023-06-01"

    def __init__(self, model: str | None = None, temperature: float = 0.9,
                 max_tokens: int = 800) -> None:
        super().__init__(
            model=model or os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6"),
            temperature=temperature,
            max_tokens=max_tokens,
        )
        self.api_key = os.getenv("ANTHROPIC_API_KEY", "")
        if not self.api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set.")

    @retry(stop=stop_after_attempt(3),
           wait=wait_exponential(multiplier=1, min=2, max=10))
    def complete(self, system: str, user: str) -> LLMResponse:
        resp = requests.post(
            self.API,
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": self.VERSION,
                "content-type": "application/json",
            },
            json={
                "model": self.model,
                "max_tokens": self.max_tokens,
                "temperature": self.temperature,
                "system": system + "\n\nRespond ONLY with valid JSON.",
                "messages": [{"role": "user", "content": user}],
            },
            timeout=90,
        )
        resp.raise_for_status()
        data = resp.json()
        text = "".join(part.get("text", "") for part in data.get("content", []))
        # Strip code fences if the model added them.
        text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        json.loads(text)
        return LLMResponse(text, self.name, self.model)
