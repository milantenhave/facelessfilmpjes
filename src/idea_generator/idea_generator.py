"""Generates viral video ideas per niche, optionally steered by analytics."""
from __future__ import annotations

import json
import random
from dataclasses import asdict, dataclass, field
from typing import Iterable

from ..llm import LLMProvider
from ..utils.logger import get_logger

log = get_logger(__name__)

IDEA_SYSTEM = """You are a senior short-form video strategist. IDEA_BATCH mode.
Return ONLY valid JSON in the shape:
{"ideas": [{"hook": str, "topic": str, "emotion": str, "angle": str}, ...]}

Guidelines:
- Hooks must stop the scroll in <3 seconds.
- Rotate emotions across curiosity, fear, motivation, surprise, awe, urgency.
- Mix angles: contrarian, myth-bust, story, listicle, shock-stat, how-to.
- Target platforms: TikTok, Reels, Shorts.
"""


@dataclass
class Idea:
    hook: str
    topic: str
    emotion: str
    angle: str = "contrarian"
    niche: str = ""
    tone: str = ""
    score: float = 0.0
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


class IdeaGenerator:
    def __init__(self, llm: LLMProvider, cfg: dict) -> None:
        self.llm = llm
        self.cfg = cfg
        self.per_niche = int(cfg.get("ideas_per_niche", 6))

    def run(self, winning_patterns: Iterable[str] | None = None) -> list[Idea]:
        ideas: list[Idea] = []
        patterns = list(winning_patterns or [])
        for niche in self.cfg.get("niches", []):
            ideas.extend(self._ideas_for_niche(niche, patterns))
        random.shuffle(ideas)
        log.info("idea_generator produced %d ideas", len(ideas))
        return ideas

    def _ideas_for_niche(self, niche: dict, patterns: list[str]) -> list[Idea]:
        name = niche.get("name", "general")
        tone = niche.get("tone", "neutral")
        reading_level = (niche.get("reading_level") or "simple").lower()
        emotions = ", ".join(niche.get("emotions", [])) or "curiosity"
        hint = ""
        if patterns:
            hint = "\nPrefer these proven patterns:\n- " + "\n- ".join(patterns[:5])

        level_hint = {
            "simple":   "Hooks must be easy for a 10-year-old. Short, everyday words only.",
            "normal":   "Hooks in clear conversational English, 8th-grade level.",
            "advanced": "Confident adult register is fine; niche vocabulary allowed.",
        }.get(reading_level, "Hooks must be easy for a 10-year-old. Short, everyday words only.")

        system = IDEA_SYSTEM + "\n" + level_hint
        user = (
            f"niche=\"{name}\"\n"
            f"tone=\"{tone}\"\n"
            f"emotions=\"{emotions}\"\n"
            f"reading_level={reading_level}\n"
            f"count={self.per_niche}\n"
            f"language={self.cfg.get('language', 'en')}"
            f"{hint}"
        )
        try:
            resp = self.llm.complete(system, user)
            data = json.loads(resp.text)
            raw = data.get("ideas", [])
        except Exception as exc:   # noqa: BLE001
            log.warning("idea generation failed for %s (%s) — skipping.", name, exc)
            return []

        out: list[Idea] = []
        for item in raw:
            if not isinstance(item, dict) or "hook" not in item:
                continue
            out.append(Idea(
                hook=str(item.get("hook", "")).strip(),
                topic=str(item.get("topic", name)).strip(),
                emotion=str(item.get("emotion", "curiosity")).strip(),
                angle=str(item.get("angle", "contrarian")).strip(),
                niche=name,
                tone=tone,
            ))
        return out
