"""Abstract LLM provider."""
from __future__ import annotations

import abc
from dataclasses import dataclass


@dataclass
class LLMResponse:
    text: str
    provider: str
    model: str


class LLMProvider(abc.ABC):
    name: str = "abstract"

    def __init__(self, model: str = "", temperature: float = 0.9,
                 max_tokens: int = 800) -> None:
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens

    @abc.abstractmethod
    def complete(self, system: str, user: str) -> LLMResponse:
        """Run a single-shot completion. Must not raise on transient errors;
        providers are expected to raise only when configuration is invalid.
        """
