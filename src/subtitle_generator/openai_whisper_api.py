"""OpenAI Whisper API transcription for real word-level subtitle timing.

Cost: $0.006 / minute (~$0.003 per 30s video). Output is verbose JSON with
per-word timestamps we can feed straight into Creatomate as caption cues.
"""
from __future__ import annotations

import os
from pathlib import Path

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from ..utils.logger import get_logger

log = get_logger(__name__)


class OpenAIWhisperAPI:
    API = "https://api.openai.com/v1/audio/transcriptions"

    def __init__(self, model: str = "whisper-1") -> None:
        self.api_key = os.getenv("OPENAI_API_KEY", "")
        self.model = model

    def available(self) -> bool:
        return bool(self.api_key)

    @retry(stop=stop_after_attempt(3),
           wait=wait_exponential(multiplier=1, min=2, max=8))
    def word_timestamps(self, audio_path: Path,
                        language: str = "en") -> list[tuple[float, float, str]]:
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY missing")
        if not audio_path.exists() or audio_path.stat().st_size == 0:
            raise RuntimeError(f"audio file missing: {audio_path}")

        with audio_path.open("rb") as fh:
            resp = requests.post(
                self.API,
                headers={"Authorization": f"Bearer {self.api_key}"},
                data={
                    "model": self.model,
                    "language": language or "en",
                    "response_format": "verbose_json",
                    "timestamp_granularities[]": "word",
                },
                files={"file": (audio_path.name, fh,
                                "audio/mpeg" if audio_path.suffix == ".mp3"
                                else "audio/wav")},
                timeout=180,
            )
        if resp.status_code >= 400:
            raise RuntimeError(f"Whisper API error {resp.status_code}: "
                               f"{resp.text[:400]}")
        data = resp.json()
        words = data.get("words") or []
        out: list[tuple[float, float, str]] = []
        for w in words:
            try:
                start = float(w["start"])
                end = float(w["end"])
                text = str(w.get("word") or "").strip()
                if text:
                    out.append((start, end, text))
            except (KeyError, TypeError, ValueError):
                continue
        log.info("Whisper API returned %d words for %s", len(out), audio_path.name)
        return out
