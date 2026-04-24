"""Turn an Idea into a short, retention-optimised script."""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field

from ..idea_generator import Idea
from ..llm import LLMProvider
from ..utils.logger import get_logger

log = get_logger(__name__)

SCRIPT_SYSTEM = """You write faceless short-form video SCRIPTs for TikTok,
Reels and Shorts. Return ONLY valid JSON:
{"hook": str, "body": str, "payoff": str, "cta": str,
 "script": str, "keywords": [str, ...]}

Rules:
- Target 15–30 seconds when read aloud (~50–90 words total).
- Hook is 1 sentence, <=12 words, stops the scroll.
- Body is 2–3 punchy sentences delivering the insight.
- Payoff is 1 sentence that lands the idea.
- CTA is 1 short line (follow / save / comment).
- Everything must be in the requested language.
- No emojis. No stage directions. Spoken conversational English.
- Provide 4–6 keywords usable for stock-footage search.
"""


@dataclass
class Script:
    idea: Idea
    hook: str
    body: str
    payoff: str
    cta: str
    full_text: str
    sentences: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    variant: str = "A"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["idea"] = self.idea.to_dict()
        return d


_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")


def split_sentences(text: str) -> list[str]:
    parts = [p.strip() for p in _SENT_SPLIT.split(text.strip()) if p.strip()]
    # Combine very short fragments with the next sentence to avoid 1-word shots.
    out: list[str] = []
    for p in parts:
        if out and len(p.split()) <= 2:
            out[-1] = f"{out[-1]} {p}"
        else:
            out.append(p)
    return out


class ScriptGenerator:
    def __init__(self, llm: LLMProvider, cfg: dict) -> None:
        self.llm = llm
        self.cfg = cfg
        self.target_seconds = int(cfg.get("video_length_seconds", 25))

    def run(self, idea: Idea, variant: str = "A") -> Script | None:
        user = (
            f"hook_seed=\"{idea.hook}\"\n"
            f"topic=\"{idea.topic}\"\n"
            f"niche=\"{idea.niche}\"\n"
            f"tone=\"{idea.tone}\"\n"
            f"emotion=\"{idea.emotion}\"\n"
            f"angle=\"{idea.angle}\"\n"
            f"variant=\"{variant}\"\n"
            f"target_seconds={self.target_seconds}\n"
            f"language={self.cfg.get('language', 'en')}"
        )
        try:
            resp = self.llm.complete(SCRIPT_SYSTEM, user)
            data = json.loads(resp.text)
        except Exception as exc:   # noqa: BLE001
            log.error("script generation failed for idea %r: %s", idea.hook, exc)
            return None

        hook = data.get("hook", idea.hook)
        body = data.get("body", "")
        payoff = data.get("payoff", "")
        cta = data.get("cta", "")
        full = data.get("script") or " ".join(p for p in [hook, body, payoff, cta] if p)

        return Script(
            idea=idea,
            hook=hook.strip(),
            body=body.strip(),
            payoff=payoff.strip(),
            cta=cta.strip(),
            full_text=full.strip(),
            sentences=split_sentences(full),
            keywords=data.get("keywords") or idea.topic.split(),
            variant=variant,
        )
