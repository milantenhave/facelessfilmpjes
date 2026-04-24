"""Creatomate cloud renderer.

Builds a 9:16 composition with:
- background video clips (fit=cover, subtle Ken Burns, crossfades)
- voice-over (our OpenAI TTS MP3, served from PUBLIC_BASE_URL/media/tmp/<t>)
- optional royalty-free background music (low volume)
- word-by-word animated captions with keyword highlighting

Key visual tweaks for a "pro" look:
- Each clip section uses a random `trim_start` so repeats of the same source
  still show different footage.
- Captions are uppercase, centered slightly above the lower third, with
  pop-in animation and colour highlights on long/keyword-like tokens.
- Crossfades of 0.3s between sections give smoother pacing.
"""
from __future__ import annotations

import os
import random
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from ..subtitle_generator import SubtitleCue
from ..utils.logger import get_logger

log = get_logger(__name__)


@dataclass
class MediaSection:
    url: str
    time: float
    duration: float
    fit: str = "cover"
    native_duration: float = 0.0     # of the source, for smart trim
    is_video: bool = True


@dataclass
class RenderRequest:
    width: int
    height: int
    duration: float
    voice_url: str
    sections: list[MediaSection]
    word_cues: list[SubtitleCue]
    music_url: Optional[str] = None
    brand_color: str = "#FFD400"
    text_color: str = "#FFFFFF"
    font_family: str = "Montserrat"
    fps: int = 30


@dataclass
class RenderResult:
    id: str
    status: str
    url: str
    duration: float


class CreatomateRenderer:
    API_BASE = "https://api.creatomate.com/v1"

    def __init__(self, api_key: Optional[str] = None,
                 poll_interval: float = 4.0,
                 poll_timeout: float = 600.0) -> None:
        self.api_key = api_key or os.getenv("CREATOMATE_API_KEY", "")
        self.poll_interval = poll_interval
        self.poll_timeout = poll_timeout

    def available(self) -> bool:
        return bool(self.api_key)

    # ------------------------------------------------------------------
    @retry(stop=stop_after_attempt(3),
           wait=wait_exponential(multiplier=1, min=2, max=15))
    def render(self, req: RenderRequest, out_path: Path,
               progress_cb=None) -> RenderResult:
        if not self.api_key:
            raise RuntimeError("CREATOMATE_API_KEY is not set.")

        source = self._build_source(req)
        log.info("creatomate: submitting render "
                 "(duration=%.2fs, sections=%d, words=%d)",
                 req.duration, len(req.sections), len(req.word_cues))

        resp = requests.post(
            f"{self.API_BASE}/renders",
            headers=self._headers(),
            json={"source": source, "output_format": "mp4"},
            timeout=60,
        )
        resp.raise_for_status()
        payload = resp.json()
        if isinstance(payload, list):
            payload = payload[0]
        render_id = payload["id"]

        result = self._poll(render_id, progress_cb=progress_cb)
        self._download(result.url, out_path)
        return result

    # ------------------------------------------------------------------
    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _poll(self, render_id: str, progress_cb=None) -> RenderResult:
        start = time.time()
        last_pct = 0.0
        while True:
            r = requests.get(f"{self.API_BASE}/renders/{render_id}",
                             headers=self._headers(), timeout=30)
            r.raise_for_status()
            data = r.json()
            status = data.get("status", "planned")
            if status in ("succeeded", "failed"):
                if status == "failed":
                    raise RuntimeError(
                        f"Creatomate render failed: "
                        f"{data.get('error_message', data)}"
                    )
                return RenderResult(
                    id=render_id, status=status,
                    url=data.get("url", ""),
                    duration=float(data.get("duration", 0) or 0),
                )
            elapsed = time.time() - start
            pct = min(90.0, (elapsed / 60.0) * 100)
            if progress_cb and pct - last_pct >= 5:
                progress_cb(pct)
                last_pct = pct
            if elapsed > self.poll_timeout:
                raise TimeoutError(f"Creatomate render timed out: {render_id}")
            time.sleep(self.poll_interval)

    @staticmethod
    def _download(url: str, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with requests.get(url, stream=True, timeout=180) as r:
            r.raise_for_status()
            with dest.open("wb") as fh:
                for chunk in r.iter_content(chunk_size=1024 * 64):
                    if chunk:
                        fh.write(chunk)

    # ------------------------------------------------------------------ composition
    def _build_source(self, req: RenderRequest) -> dict:
        elements: list[dict] = []

        # --- Background clips on track 1 ------------------------------
        for i, section in enumerate(req.sections):
            trim_start = self._pick_trim_start(section)
            base: dict = {
                "type": "video" if section.is_video else "image",
                "source": section.url,
                "track": 1,
                "time": round(section.time, 3),
                "duration": round(section.duration, 3),
                "fit": section.fit,
                "volume": 0,
            }
            if section.is_video:
                base["trim_start"] = round(trim_start, 3)
                base["trim_duration"] = round(section.duration, 3)
            # Subtle slow zoom on every section.
            anims = [{
                "time": "start",
                "duration": round(section.duration, 3),
                "easing": "linear",
                "type": "scale",
                "scope": "element",
                "start_scale": "100%",
                "end_scale": "112%",
            }]
            # Crossfade between sections.
            if i > 0:
                anims.append({
                    "time": "start",
                    "duration": 0.3,
                    "type": "fade",
                    "easing": "linear",
                })
            base["animations"] = anims
            elements.append(base)

        # Darken layer for caption legibility
        elements.append({
            "type": "shape",
            "track": 2,
            "time": 0,
            "duration": round(req.duration, 3),
            "x": "50%", "y": "50%",
            "width": "100%", "height": "100%",
            "fill_color": "rgba(0,0,0,0.28)",
        })

        # --- Voice-over on track 3 ------------------------------------
        elements.append({
            "type": "audio",
            "source": req.voice_url,
            "track": 3,
            "time": 0,
        })

        # --- Background music on track 4 ------------------------------
        if req.music_url:
            elements.append({
                "type": "audio",
                "source": req.music_url,
                "track": 4,
                "time": 0,
                "duration": round(req.duration, 3),
                "volume": "7%",
                "audio_fade_in": 0.6,
                "audio_fade_out": 1.0,
                "loop": True,
            })

        # --- Word-by-word captions on track 5 -------------------------
        for cue in req.word_cues:
            word = _clean_caption(cue.text)
            if not word:
                continue
            start = round(cue.start, 3)
            dur = max(0.16, round(cue.end - cue.start + 0.06, 3))
            highlight = _is_highlight(word)
            fill = req.brand_color if highlight else req.text_color
            elements.append({
                "type": "text",
                "text": word.upper(),
                "track": 5,
                "time": start,
                "duration": dur,
                "x": "50%",
                "y": "68%",
                "width": "88%",
                "height": "22%",
                "x_alignment": "50%",
                "y_alignment": "50%",
                "font_family": req.font_family,
                "font_weight": "900",
                "font_size": "11 vmin" if highlight else "10 vmin",
                "line_height": "110%",
                "fill_color": fill,
                "stroke_color": "#000000",
                "stroke_width": "0.55 vmin",
                "shadow_color": "rgba(0,0,0,0.9)",
                "shadow_blur": "1 vmin",
                "shadow_y": "0.35 vmin",
                "animations": [
                    {
                        "time": "start",
                        "duration": 0.16,
                        "easing": "back-out",
                        "type": "scale",
                        "start_scale": "58%",
                        "end_scale": "100%",
                    },
                    {
                        "time": "start",
                        "duration": 0.12,
                        "type": "fade",
                        "easing": "linear",
                    },
                ],
            })

        return {
            "output_format": "mp4",
            "frame_rate": req.fps,
            "width": req.width,
            "height": req.height,
            "duration": round(req.duration, 3),
            "elements": elements,
        }

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _pick_trim_start(section: MediaSection) -> float:
        """Choose an in-point inside the source clip.

        If the source is long enough, start somewhere in the first half to show
        a different moment each time; otherwise start at 0.
        """
        native = section.native_duration or section.duration * 2
        safe_end = max(0.0, native - section.duration - 0.1)
        if safe_end <= 0.2:
            return 0.0
        return round(random.uniform(0.0, min(safe_end, 3.0)), 3)


_CAPTION_RX = re.compile(r"[^\w\-'\s]", flags=re.UNICODE)


def _clean_caption(text: str) -> str:
    t = (text or "").strip()
    t = _CAPTION_RX.sub("", t)
    return t.strip()


def _is_highlight(word: str) -> bool:
    """Decide whether this word should get the brand accent colour."""
    w = word.strip()
    if not w:
        return False
    if any(ch.isdigit() for ch in w):
        return True
    if len(w) >= 8:
        return True
    return w.upper() in {
        "NEVER", "ALWAYS", "SECRET", "TRUTH", "STOP", "NOW", "MONEY",
        "WARNING", "FREE", "BEST", "WORST", "PROVEN", "SCIENCE",
    }
