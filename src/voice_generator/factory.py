"""Pick the right TTS backend at runtime.

Ensures the correct file extension is used per backend:
- OpenAI TTS writes .mp3
- Local fallbacks write .wav

Without this fix, a .mp3-named file can actually contain WAV bytes and confuse
downstream consumers (Whisper, Creatomate).
"""
from __future__ import annotations

import os
from pathlib import Path

from ..utils.logger import get_logger
from .openai_tts import OpenAITTS
from .voice_generator import VoiceClip, VoiceGenerator

log = get_logger(__name__)


class TTSFactory:
    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg
        self.local = VoiceGenerator(cfg)
        tts_cfg = cfg.get("tts", {})
        engine = (tts_cfg.get("engine") or os.getenv("TTS_ENGINE", "")).lower()
        voice = tts_cfg.get("voice") or os.getenv("OPENAI_TTS_VOICE")
        model = tts_cfg.get("openai_model") or os.getenv("OPENAI_TTS_MODEL")
        speed = float(tts_cfg.get("speed", 1.0))

        self._openai: OpenAITTS | None = None
        if engine in ("openai", "openai_tts", "") \
                and os.getenv("OPENAI_API_KEY"):
            self._openai = OpenAITTS(voice=voice, model=model, speed=speed)

    def synthesize(self, text: str, out_path: Path,
                   voice_override: str | None = None) -> VoiceClip:
        if self._openai and self._openai.available():
            target = out_path.with_suffix(".mp3")
            try:
                return self._openai.synthesize(text, target,
                                               voice_override=voice_override)
            except Exception as exc:  # noqa: BLE001
                log.warning("OpenAI TTS failed (%s) — falling back to local.",
                            _unwrap(exc))
        target = out_path.with_suffix(".wav")
        return self.local.synthesize(text, target)


def _unwrap(exc: BaseException) -> str:
    """Surface the real error hiding behind tenacity.RetryError."""
    try:
        from tenacity import RetryError
        if isinstance(exc, RetryError) and exc.last_attempt is not None:
            inner = exc.last_attempt.exception()
            if inner is not None:
                return f"{type(inner).__name__}: {inner}"
    except Exception:
        pass
    return f"{type(exc).__name__}: {exc}"
