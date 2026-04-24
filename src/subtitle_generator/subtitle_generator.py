"""Generate SRT subtitles + word-level cues from the generated voice-over.

Strategy:
    1. OpenAI Whisper API (best quality, $0.003/30s) — default when
       OPENAI_API_KEY is set.
    2. Local openai-whisper tiny model (if installed) — free but heavy on RAM.
    3. Even-split fallback — always works, used as a safety net.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from ..script_generator import Script
from ..utils.logger import get_logger
from ..voice_generator import VoiceClip
from .openai_whisper_api import OpenAIWhisperAPI

log = get_logger(__name__)


@dataclass
class SubtitleCue:
    start: float
    end: float
    text: str


def _fmt(td: float) -> str:
    t = timedelta(seconds=max(0.0, td))
    total = int(t.total_seconds())
    ms = int((t.total_seconds() - total) * 1000)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def cues_to_srt(cues: list[SubtitleCue]) -> str:
    lines: list[str] = []
    for i, c in enumerate(cues, start=1):
        lines.append(str(i))
        lines.append(f"{_fmt(c.start)} --> {_fmt(c.end)}")
        lines.append(c.text.strip())
        lines.append("")
    return "\n".join(lines)


class SubtitleGenerator:
    def __init__(self, cfg: dict) -> None:
        sub = cfg.get("subtitles", {})
        self.enabled = bool(sub.get("enabled", True))
        self.mode = sub.get("mode", "word")  # word | sentence
        self.language = cfg.get("language", "en")
        self.whisper_api = OpenAIWhisperAPI() \
            if os.getenv("OPENAI_API_KEY") else None

    def run(self, script: Script, voice: VoiceClip,
            out_path: Path) -> list[SubtitleCue]:
        if not self.enabled:
            out_path.write_text("", "utf-8")
            return []

        cues = (
            self._openai_whisper(voice)
            or self._local_whisper(voice)
            or self._even_align(script, voice)
        )
        if self.mode == "sentence":
            cues = self._group_into_sentences(cues)

        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(cues_to_srt(cues), "utf-8")
        log.info("generated %d subtitle cues (%s) -> %s",
                 len(cues), self.mode, out_path)
        return cues

    # ------------------------------------------------------------------ aligners
    def _openai_whisper(self, voice: VoiceClip) -> list[SubtitleCue]:
        if not self.whisper_api or not self.whisper_api.available():
            return []
        try:
            words = self.whisper_api.word_timestamps(
                Path(voice.path), language=self.language)
        except Exception as exc:  # noqa: BLE001
            log.warning("OpenAI Whisper API failed (%s) — trying next.", exc)
            return []
        return [SubtitleCue(s, e, t) for s, e, t in words]

    def _local_whisper(self, voice: VoiceClip) -> list[SubtitleCue]:
        try:
            import whisper  # type: ignore
        except Exception:   # noqa: BLE001
            return []
        try:
            model = whisper.load_model("tiny")
            result = model.transcribe(str(voice.path),
                                      word_timestamps=True,
                                      fp16=False, verbose=False,
                                      language=self.language)
        except Exception as exc:  # noqa: BLE001
            log.warning("local whisper failed (%s).", exc)
            return []

        words: list[SubtitleCue] = []
        for seg in result.get("segments", []):
            for w in seg.get("words") or []:
                tok = (w.get("word") or "").strip()
                if not tok:
                    continue
                words.append(SubtitleCue(float(w["start"]),
                                         float(w["end"]), tok))
        return words

    def _even_align(self, script: Script,
                    voice: VoiceClip) -> list[SubtitleCue]:
        duration = voice.duration or max(
            1.0, len(script.full_text.split()) / 2.6)
        tokens = script.full_text.split()
        if not tokens:
            return []
        slot = duration / len(tokens)
        return [
            SubtitleCue(i * slot, (i + 1) * slot, tok)
            for i, tok in enumerate(tokens)
        ]

    # ------------------------------------------------------------------ grouping
    @staticmethod
    def _group_into_sentences(cues: list[SubtitleCue]) -> list[SubtitleCue]:
        out: list[SubtitleCue] = []
        chunk: list[SubtitleCue] = []
        for c in cues:
            chunk.append(c)
            if c.text.rstrip().endswith((".", "!", "?")) or len(chunk) >= 8:
                out.append(SubtitleCue(
                    chunk[0].start, chunk[-1].end,
                    " ".join(x.text for x in chunk).strip(),
                ))
                chunk = []
        if chunk:
            out.append(SubtitleCue(
                chunk[0].start, chunk[-1].end,
                " ".join(x.text for x in chunk).strip(),
            ))
        return out
