"""Text-to-speech layer with pluggable engines.

Order of preference:
    coqui   -> high quality, fully offline after model download
    pyttsx3 -> works anywhere, lightweight, offline
    espeak  -> CLI fallback, almost always present on Linux
    silence -> last resort, produces timed silence so the pipeline never breaks
"""
from __future__ import annotations

import shutil
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path

from ..utils.logger import get_logger

log = get_logger(__name__)


@dataclass
class VoiceClip:
    path: Path
    duration: float
    engine: str


class VoiceGenerator:
    def __init__(self, cfg: dict) -> None:
        tts = cfg.get("tts", {})
        self.engine_pref = (tts.get("engine") or "pyttsx3").lower()
        self.rate = int(tts.get("rate", 175))
        self.speed = float(tts.get("speed", 1.0))
        self.voice = tts.get("voice", "default")
        self.coqui_model = tts.get("coqui_model",
                                   "tts_models/en/ljspeech/tacotron2-DDC")

    def synthesize(self, text: str, out_path: Path) -> VoiceClip:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        text = text.strip() or "."

        attempts = [self.engine_pref, "pyttsx3", "espeak", "silence"]
        seen: set[str] = set()
        for engine in attempts:
            if engine in seen:
                continue
            seen.add(engine)
            try:
                if engine == "coqui":
                    return self._coqui(text, out_path)
                if engine == "pyttsx3":
                    return self._pyttsx3(text, out_path)
                if engine == "espeak":
                    return self._espeak(text, out_path)
                if engine == "silence":
                    return self._silence(text, out_path)
            except Exception as exc:   # noqa: BLE001
                log.warning("TTS engine %s failed: %s", engine, exc)
        # Should be unreachable — silence always succeeds.
        raise RuntimeError("all TTS engines failed")

    # -- engines --------------------------------------------------------
    def _coqui(self, text: str, out_path: Path) -> VoiceClip:
        from TTS.api import TTS  # type: ignore

        tts = TTS(self.coqui_model, progress_bar=False)
        tts.tts_to_file(text=text, file_path=str(out_path), speed=self.speed)
        return VoiceClip(out_path, self._wav_duration(out_path), "coqui")

    def _pyttsx3(self, text: str, out_path: Path) -> VoiceClip:
        import pyttsx3  # type: ignore

        engine = pyttsx3.init()
        engine.setProperty("rate", self.rate)
        if self.voice and self.voice != "default":
            for v in engine.getProperty("voices"):
                if self.voice.lower() in (v.id + v.name).lower():
                    engine.setProperty("voice", v.id)
                    break
        engine.save_to_file(text, str(out_path))
        engine.runAndWait()
        return VoiceClip(out_path, self._wav_duration(out_path), "pyttsx3")

    def _espeak(self, text: str, out_path: Path) -> VoiceClip:
        binary = shutil.which("espeak-ng") or shutil.which("espeak")
        if not binary:
            raise RuntimeError("espeak not installed")
        subprocess.run(
            [binary, "-s", str(self.rate), "-w", str(out_path), text],
            check=True, capture_output=True,
        )
        return VoiceClip(out_path, self._wav_duration(out_path), "espeak")

    def _silence(self, text: str, out_path: Path) -> VoiceClip:
        """Produce a silent WAV of roughly the right length based on word count."""
        words = max(1, len(text.split()))
        duration = max(1.5, words / 2.6)  # ~2.6 words / second
        self._write_silence(out_path, duration)
        log.warning("using silent placeholder audio (%.2fs) for: %s...",
                    duration, text[:60])
        return VoiceClip(out_path, duration, "silence")

    # -- helpers --------------------------------------------------------
    @staticmethod
    def _wav_duration(path: Path) -> float:
        try:
            with wave.open(str(path), "rb") as w:
                frames = w.getnframes()
                rate = w.getframerate() or 1
                return frames / float(rate)
        except wave.Error:
            # Fall back to ffprobe for non-PCM files.
            ffprobe = shutil.which("ffprobe")
            if not ffprobe:
                return 0.0
            result = subprocess.run(
                [ffprobe, "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
                capture_output=True, text=True, check=False,
            )
            try:
                return float(result.stdout.strip())
            except ValueError:
                return 0.0

    @staticmethod
    def _write_silence(path: Path, duration: float, sample_rate: int = 22050) -> None:
        n_frames = int(duration * sample_rate)
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sample_rate)
            w.writeframes(b"\x00\x00" * n_frames)
