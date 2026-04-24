"""Deterministic offline provider.

Used when no API keys are configured. It produces structured, templated content
so the rest of the pipeline can still run end-to-end without a real LLM.
"""
from __future__ import annotations

import hashlib
import json
import random
from typing import Any

from .base import LLMProvider, LLMResponse

HOOK_TEMPLATES = [
    "You won't believe what {topic} actually does to your brain.",
    "Stop scrolling. This one thing about {topic} will change your life.",
    "Nobody talks about this side of {topic} — and it's costing you.",
    "Three seconds. That's all it takes to rethink {topic} forever.",
    "What if everything you knew about {topic} was wrong?",
    "Here's the {topic} secret the internet is hiding from you.",
]

BODY_TEMPLATES = [
    "Most people approach {topic} the same way every day, and get the same "
    "mediocre results. The trick is in the tiny compounding decisions.",
    "Researchers studied {topic} for decades and found one surprising pattern: "
    "the outliers did the opposite of the crowd.",
    "If you want to master {topic}, stop copying what everyone else is doing "
    "and start asking why it even works.",
]

PAYOFFS = [
    "Small shifts, massive outcomes — that's the real game.",
    "Apply this tonight and you'll feel the difference tomorrow.",
    "The compounding alone will outpace 99% of people.",
]

CTAS = [
    "Follow for one uncomfortable truth per day.",
    "Save this before the algorithm buries it.",
    "Share this with someone who needs to hear it.",
]


class TemplateProvider(LLMProvider):
    name = "template"

    def __init__(self, *_, **__) -> None:
        super().__init__(model="template-v1", temperature=0.0, max_tokens=0)

    def _seed(self, text: str) -> random.Random:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        return random.Random(int.from_bytes(digest[:8], "big"))

    def complete(self, system: str, user: str) -> LLMResponse:
        rng = self._seed(system + "||" + user)
        topic = self._extract_topic(user)

        if "IDEA_BATCH" in system:
            count = self._extract_int(user, "count", 6)
            niche = self._extract_field(user, "niche", topic)
            emotions = ["curiosity", "motivation", "fear", "surprise"]
            ideas = []
            for _ in range(count):
                hook = rng.choice(HOOK_TEMPLATES).format(topic=niche)
                ideas.append({
                    "hook": hook,
                    "topic": f"{niche}: {self._mini_topic(rng)}",
                    "emotion": rng.choice(emotions),
                    "angle": rng.choice([
                        "contrarian", "listicle", "story",
                        "myth-bust", "how-to", "shock-stat",
                    ]),
                })
            payload: dict[str, Any] = {"ideas": ideas}
            return LLMResponse(json.dumps(payload), self.name, self.model)

        if "SCRIPT" in system:
            hook = rng.choice(HOOK_TEMPLATES).format(topic=topic)
            body = rng.choice(BODY_TEMPLATES).format(topic=topic)
            payoff = rng.choice(PAYOFFS)
            cta = rng.choice(CTAS)
            payload = {
                "hook": hook,
                "body": body,
                "payoff": payoff,
                "cta": cta,
                "script": f"{hook} {body} {payoff} {cta}",
                "keywords": self._keywords(topic, rng),
            }
            return LLMResponse(json.dumps(payload), self.name, self.model)

        if "CAPTION" in system:
            payload = {
                "title": f"The truth about {topic}",
                "description": (
                    f"A 25-second deep dive into {topic}. "
                    "If you felt something, follow for more."
                ),
                "hashtags": self._hashtags(topic, rng),
            }
            return LLMResponse(json.dumps(payload), self.name, self.model)

        # Generic fallback
        return LLMResponse(json.dumps({"text": user[:200]}), self.name, self.model)

    # -- helpers --------------------------------------------------------
    @staticmethod
    def _extract_field(text: str, field: str, default: str) -> str:
        marker = f"{field}="
        if marker in text:
            rest = text.split(marker, 1)[1]
            return rest.split("\n", 1)[0].strip().strip('"')
        return default

    @staticmethod
    def _extract_int(text: str, field: str, default: int) -> int:
        try:
            return int(TemplateProvider._extract_field(text, field, str(default)))
        except ValueError:
            return default

    @staticmethod
    def _extract_topic(text: str) -> str:
        return TemplateProvider._extract_field(text, "topic",
               TemplateProvider._extract_field(text, "niche", "success"))

    @staticmethod
    def _mini_topic(rng: random.Random) -> str:
        return rng.choice([
            "the 2-minute rule",
            "dopamine hijacks",
            "the anti-routine",
            "compounding losses",
            "identity shifts",
            "the 4am experiment",
        ])

    @staticmethod
    def _keywords(topic: str, rng: random.Random) -> list[str]:
        pool = ["mindset", "growth", "science", "psychology", "habits",
                "focus", "brain", "money", "winning", "discipline"]
        rng.shuffle(pool)
        return [topic, *pool[:4]]

    @staticmethod
    def _hashtags(topic: str, rng: random.Random) -> list[str]:
        base = [f"#{topic.replace(' ', '')}"]
        pool = ["#shorts", "#reels", "#tiktok", "#motivation", "#mindset",
                "#selfgrowth", "#viral", "#fyp", "#learnontiktok", "#facts",
                "#psychology", "#wealth", "#discipline"]
        rng.shuffle(pool)
        return base + pool[:12]
