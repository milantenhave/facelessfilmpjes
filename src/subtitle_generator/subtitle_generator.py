"""Generate SRT subtitles from a script + synthesized audio.

Uses Whisper for word-level timing when available; otherwise falls back to
evenly-distributing words across the audio's known duration. The fallback path
is essential because Whisper is a heavy dependency and may not be installed on
a minimal VPS.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from ..script_generator import Script
from ..utils.logger import get_logger
from ..voice_generator import VoiceClip

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

    def run(self, script: Script, voice: VoiceClip, out_path: Path) -> list[SubtitleCue]:
        if not self.enabled:
            out_path.write_text("", "utf-8")
            return []

        cues = self._whisper_align(script, voice) or self._even_align(script, voice)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(cues_to_srt(cues), "utf-8")
        log.info("generated %d subtitle cues (%s) -> %s",
                 len(cues), self.mode, out_path)
        return cues

    # -- aligners -------------------------------------------------------
    def _whisper_align(self, script: Script, voice: VoiceClip) -> list[SubtitleCue]:
        try:
            import whisper  # type: ignore
        except Exception as exc:   # noqa: BLE001
            log.info("whisper not available (%s); using even alignment.", exc)
            return []
        try:
            model = whisper.load_model("tiny")
            result = model.transcribe(str(voice.path), word_timestamps=True,
                                      fp16=False, verbose=False)
        except Exception as exc:   # noqa: BLE001
            log.warning("whisper failed (%s); using even alignment.", exc)
            return []

        words: list[tuple[float, float, str]] = []
        for seg in result.get("segments", []):
            for w in seg.get("words") or []:
                tok = (w.get("word") or "").strip()
                if not tok:
                    continue
                words.append((float(w["start"]), float(w["end"]), tok))

        if not words:
            return []

        if self.mode == "word":
            return [SubtitleCue(s, e, t) for s, e, t in words]

        # sentence mode: group ~8 words per cue, break on .?!
        cues: list[SubtitleCue] = []
        chunk: list[tuple[float, float, str]] = []
        for w in words:
            chunk.append(w)
            if w[2].endswith((".", "!", "?")) or len(chunk) >= 8:
                cues.append(SubtitleCue(chunk[0][0], chunk[-1][1],
                                        " ".join(x[2] for x in chunk).strip()))
                chunk = []
        if chunk:
            cues.append(SubtitleCue(chunk[0][0], chunk[-1][1],
                                    " ".join(x[2] for x in chunk).strip()))
        return cues

    def _even_align(self, script: Script, voice: VoiceClip) -> list[SubtitleCue]:
        duration = voice.duration or max(1.0, len(script.full_text.split()) / 2.6)
        if self.mode == "word":
            tokens = script.full_text.split()
            if not tokens:
                return []
            slot = duration / len(tokens)
            return [
                SubtitleCue(i * slot, (i + 1) * slot, tok)
                for i, tok in enumerate(tokens)
            ]

        sentences = script.sentences or [script.full_text]
        word_counts = [max(1, len(s.split())) for s in sentences]
        total_words = sum(word_counts)
        cues: list[SubtitleCue] = []
        cursor = 0.0
        for sent, count in zip(sentences, word_counts):
            span = duration * count / total_words
            cues.append(SubtitleCue(cursor, cursor + span, sent))
            cursor += span
        return cues
