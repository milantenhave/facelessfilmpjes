"""Generate titles, descriptions and hashtags for publishing."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

from ..llm import LLMProvider
from ..script_generator import Script
from ..utils.logger import get_logger

log = get_logger(__name__)

CAPTION_SYSTEM = """You craft CAPTION metadata for TikTok / Reels / Shorts.
Return ONLY valid JSON:
{"title": str, "description": str, "hashtags": [str, ...]}

Rules:
- Title <= 80 chars, punchy, no clickbait smell.
- Description 1–3 lines, include a soft CTA on its own line.
- Hashtags lowercased, prefixed with '#', no spaces, no duplicates.
- Mix broad (#fyp #reels) + niche + topic-specific tags.
"""


@dataclass
class Caption:
    title: str
    description: str
    hashtags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


class CaptionGenerator:
    def __init__(self, llm: LLMProvider, cfg: dict) -> None:
        self.llm = llm
        self.target_tags = int(cfg.get("captions", {}).get("hashtags_count", 12))
        self.include_cta = bool(cfg.get("captions", {}).get("include_cta", True))
        self.reading_level = (cfg.get("reading_level")
                              or "simple").strip().lower()

    def run(self, script: Script) -> Caption:
        level_hint = {
            "simple":   "Keep title and description in very simple words a "
                        "10-year-old or non-native speaker understands.",
            "normal":   "Conversational, everyday language.",
            "advanced": "Confident adult register, domain vocabulary allowed.",
        }.get(self.reading_level, "Keep title and description simple.")
        system = CAPTION_SYSTEM + "\n" + level_hint
        user = (
            f"topic=\"{script.idea.topic}\"\n"
            f"niche=\"{script.idea.niche}\"\n"
            f"emotion=\"{script.idea.emotion}\"\n"
            f"hook=\"{script.hook}\"\n"
            f"reading_level={self.reading_level}\n"
            f"hashtags_count={self.target_tags}\n"
            f"include_cta={'true' if self.include_cta else 'false'}\n"
            f"script=\"{script.full_text[:400]}\""
        )
        try:
            resp = self.llm.complete(system, user)
            data = json.loads(resp.text)
        except Exception as exc:   # noqa: BLE001
            log.warning("caption generation failed (%s) — falling back.", exc)
            data = {
                "title": script.hook[:80],
                "description": script.full_text[:240],
                "hashtags": [f"#{w}" for w in script.keywords[:self.target_tags]],
            }

        tags = [self._normalise_tag(t) for t in (data.get("hashtags") or [])]
        tags = list(dict.fromkeys(t for t in tags if t))[:self.target_tags]
        return Caption(
            title=str(data.get("title", script.hook))[:100],
            description=str(data.get("description", "")).strip(),
            hashtags=tags,
        )

    @staticmethod
    def _normalise_tag(raw: str) -> str:
        t = str(raw).strip().lower()
        if not t:
            return ""
        leading_hash = t.startswith("#")
        # keep only alphanumerics + underscore; matches TikTok/IG rules
        t = "".join(ch for ch in t if ch.isalnum() or ch == "_")
        if not t:
            return ""
        return "#" + t if leading_hash or not t.startswith("#") else t
