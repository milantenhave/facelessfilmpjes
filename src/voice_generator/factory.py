"""Pick the right TTS backend at runtime."""
from __future__ import annotations

import os
from pathlib import Path

from ..utils.logger import get_logger
from .openai_tts import OpenAITTS
from .voice_generator import VoiceClip, VoiceGenerator

log = get_logger(__name__)


class TTSFactory:
    """Wraps OpenAI TTS HD with the local fallback generator.

    Order:
      1. OpenAI TTS (if OPENAI_API_KEY present and TTS_ENGINE != 'local')
      2. Local pyttsx3/espeak/silent fallback via VoiceGenerator
    """

    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg
        self.local = VoiceGenerator(cfg)
        tts_cfg = cfg.get("tts", {})
        engine = (tts_cfg.get("engine") or os.getenv("TTS_ENGINE", "")).lower()
        voice = tts_cfg.get("voice") or os.getenv("OPENAI_TTS_VOICE")
        model = tts_cfg.get("openai_model") or os.getenv("OPENAI_TTS_MODEL")
        speed = float(tts_cfg.get("speed", 1.0))

        self._openai: OpenAITTS | None = None
        if engine in ("openai", "openai_tts", "") and os.getenv("OPENAI_API_KEY"):
            self._openai = OpenAITTS(voice=voice, model=model, speed=speed)

    def synthesize(self, text: str, out_path: Path,
                   voice_override: str | None = None) -> VoiceClip:
        if self._openai and self._openai.available():
            try:
                return self._openai.synthesize(text, out_path,
                                               voice_override=voice_override)
            except Exception as exc:  # noqa: BLE001
                log.warning("OpenAI TTS failed (%s) — falling back to local.",
                            exc)
        return self.local.synthesize(text, out_path)
