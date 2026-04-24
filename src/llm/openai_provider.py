"""OpenAI-compatible provider (works with OpenAI + any drop-in like Together)."""
from __future__ import annotations

import json
import os

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from .base import LLMProvider, LLMResponse


class OpenAIProvider(LLMProvider):
    name = "openai"

    def __init__(self, model: str | None = None, temperature: float = 0.9,
                 max_tokens: int = 800,
                 base_url: str = "https://api.openai.com/v1") -> None:
        super().__init__(
            model=model or os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            temperature=temperature,
            max_tokens=max_tokens,
        )
        self.api_key = os.getenv("OPENAI_API_KEY", "")
        self.base_url = base_url.rstrip("/")
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY is not set.")

    @retry(stop=stop_after_attempt(3),
           wait=wait_exponential(multiplier=1, min=2, max=10))
    def complete(self, system: str, user: str) -> LLMResponse:
        resp = requests.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": self.model,
                "temperature": self.temperature,
                "max_tokens": self.max_tokens,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
            timeout=90,
        )
        resp.raise_for_status()
        data = resp.json()
        text = data["choices"][0]["message"]["content"]
        # validate JSON-ness early; provider contracted for JSON
        json.loads(text)
        return LLMResponse(text, self.name, self.model)
