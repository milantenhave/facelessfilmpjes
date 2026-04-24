"""Turn an Idea into a short, retention-optimised script."""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field

from ..idea_generator import Idea
from ..llm import LLMProvider
from ..utils.logger import get_logger

log = get_logger(__name__)

SCRIPT_SYSTEM = """You write short-form scripts that go viral on TikTok,
Reels and YouTube Shorts. Average retention > 80% is the bar. Return ONLY
valid JSON:
{"hook": str, "body": str, "payoff": str, "cta": str,
 "script": str, "keywords": [str, ...]}

The HOOK (first 2 seconds) is life or death. Make it:
- <= 10 words, 1 sentence
- Pattern-interrupt: number, shock claim, direct callout, or contradiction
  Good starters: "Stop…", "Nobody tells you…", "95% of people…", "Here is
  the one thing…", "This cost me…", "You are doing ___ wrong"
- Never "Hey guys", "Did you know", "In today's video", "Hi, I'm…"

BODY: 2–3 sentences, each <= 14 words. Deliver ONE concrete insight with
proof (study, stat, analogy, tiny story). No vague motivational fluff.

PAYOFF: 1 sentence that reframes the insight as a micro-action the viewer
can take TODAY.

CTA: <= 7 words. One of: Follow, Save, Comment "YES", Share this.

Rules across the board:
- Total length 55–85 words. Count words.
- Spoken English, contractions fine, no corporate register.
- Zero emojis, zero stage directions, zero brackets, zero asterisks.
- `script` = hook + body + payoff + cta, joined by single spaces. That is
  exactly what the voice-over reads.
- `keywords` = 5–7 visually concrete nouns for stock footage. Each must be
  something a camera can film (e.g. "running shoes", "stock chart",
  "city skyline at night"). Never abstract ("success", "mindset").
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
