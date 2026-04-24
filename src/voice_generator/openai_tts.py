"""OpenAI TTS HD provider — used by default for production."""
from __future__ import annotations

import os
import wave
from pathlib import Path
from typing import Optional

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from ..utils.logger import get_logger
from .voice_generator import VoiceClip

log = get_logger(__name__)


class OpenAITTS:
    """Calls the OpenAI audio API, writes an mp3 or wav, reports duration."""

    API = "https://api.openai.com/v1/audio/speech"

    # Recommended voices for faceless reels:
    #   alloy, echo, fable, onyx, nova, shimmer, ash, sage, verse
    DEFAULT_VOICE = "nova"
    DEFAULT_MODEL = "tts-1-hd"       # tts-1 (cheaper) or tts-1-hd (better)

    def __init__(self, *, voice: Optional[str] = None,
                 model: Optional[str] = None,
                 speed: float = 1.0,
                 fmt: str = "mp3") -> None:
        self.api_key = os.getenv("OPENAI_API_KEY", "")
        self.voice = voice or os.getenv("OPENAI_TTS_VOICE") or self.DEFAULT_VOICE
        self.model = model or os.getenv("OPENAI_TTS_MODEL") or self.DEFAULT_MODEL
        self.speed = max(0.25, min(4.0, float(speed)))
        self.fmt = fmt

    def available(self) -> bool:
        return bool(self.api_key)

    @retry(stop=stop_after_attempt(3),
           wait=wait_exponential(multiplier=1, min=2, max=10))
    def synthesize(self, text: str, out_path: Path,
                   voice_override: Optional[str] = None) -> VoiceClip:
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY missing for OpenAI TTS.")
        out_path.parent.mkdir(parents=True, exist_ok=True)

        models_to_try = [self.model]
        if self.model == "tts-1-hd":
            models_to_try.append("tts-1")    # fallback if HD is not enabled

        last_err = ""
        for model in models_to_try:
            resp = requests.post(
                self.API,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "voice": voice_override or self.voice,
                    "input": text,
                    "speed": self.speed,
                    "response_format": self.fmt,
                },
                timeout=120,
            )
            if resp.status_code < 400:
                out_path.write_bytes(resp.content)
                duration = self._probe_duration(out_path, text)
                log.info("OpenAI TTS wrote %s (%.2fs, voice=%s, model=%s)",
                         out_path, duration, self.voice, model)
                return VoiceClip(path=out_path, duration=duration,
                                 engine=f"openai:{model}:{self.voice}")
            last_err = f"{resp.status_code}: {resp.text[:400]}"
            log.warning("OpenAI TTS %s rejected (%s); trying next model",
                        model, last_err)
        raise RuntimeError(f"OpenAI TTS error {last_err}")

    @staticmethod
    def _probe_duration(path: Path, text: str) -> float:
        # Fast estimate via ffprobe if available, else fall back to
        # a word-count heuristic that's close enough for pipeline sequencing.
        import shutil
        import subprocess

        ffprobe = shutil.which("ffprobe")
        if ffprobe:
            result = subprocess.run(
                [ffprobe, "-v", "error",
                 "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
                capture_output=True, text=True, check=False,
            )
            try:
                return float(result.stdout.strip())
            except ValueError:
                pass

        # For WAV files we can read it directly.
        try:
            with wave.open(str(path), "rb") as w:
                return w.getnframes() / float(w.getframerate() or 1)
        except Exception:  # noqa: BLE001
            pass

        words = max(1, len(text.split()))
        return words / 2.6
