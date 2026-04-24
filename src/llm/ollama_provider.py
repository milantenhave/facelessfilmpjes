"""Local Ollama provider for fully-offline LLM use."""
from __future__ import annotations

import json
import os

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from .base import LLMProvider, LLMResponse


class OllamaProvider(LLMProvider):
    name = "ollama"

    def __init__(self, model: str | None = None, temperature: float = 0.9,
                 max_tokens: int = 800) -> None:
        super().__init__(
            model=model or os.getenv("OLLAMA_MODEL", "llama3"),
            temperature=temperature,
            max_tokens=max_tokens,
        )
        self.host = os.getenv("OLLAMA_HOST", "http://localhost:11434").rstrip("/")

    @retry(stop=stop_after_attempt(3),
           wait=wait_exponential(multiplier=1, min=2, max=10))
    def complete(self, system: str, user: str) -> LLMResponse:
        resp = requests.post(
            f"{self.host}/api/chat",
            json={
                "model": self.model,
                "stream": False,
                "format": "json",
                "options": {
                    "temperature": self.temperature,
                    "num_predict": self.max_tokens,
                },
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
            timeout=120,
        )
        resp.raise_for_status()
        data = resp.json()
        text = data.get("message", {}).get("content", "").strip()
        json.loads(text)
        return LLMResponse(text, self.name, self.model)
