"""Creatomate cloud renderer.

Sends a JSON `source` describing a 9:16 composition (background clips,
voice-over, optional music, word-by-word animated captions) and polls for
the finished MP4.

All media referenced by Creatomate must be reachable at a public URL. Stock
footage from Pexels/Pixabay is already public. The voice-over is served from
our FastAPI `/media/tmp/<uuid>` endpoint with a short-lived signed token.
"""
from __future__ import annotations

import os
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
    """One contiguous background clip covering part of the timeline."""
    url: str
    time: float         # start time on the master timeline
    duration: float
    fit: str = "cover"


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
    """Thin wrapper around the Creatomate REST API.

    Docs: https://creatomate.com/docs/api/rest-api/introduction
    """

    API_BASE = "https://api.creatomate.com/v1"

    def __init__(self, api_key: Optional[str] = None,
                 poll_interval: float = 4.0,
                 poll_timeout: float = 600.0) -> None:
        self.api_key = api_key or os.getenv("CREATOMATE_API_KEY", "")
        self.poll_interval = poll_interval
        self.poll_timeout = poll_timeout

    def available(self) -> bool:
        return bool(self.api_key)

    # -- public API ----------------------------------------------------
    @retry(stop=stop_after_attempt(3),
           wait=wait_exponential(multiplier=1, min=2, max=15))
    def render(self, req: RenderRequest, out_path: Path,
               progress_cb=None) -> RenderResult:
        if not self.api_key:
            raise RuntimeError("CREATOMATE_API_KEY is not set.")

        source = self._build_source(req)
        log.info("creatomate: submitting render (duration=%.2fs, elements=%d)",
                 req.duration, len(source["elements"]))

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

    # -- helpers -------------------------------------------------------
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
                    raise RuntimeError(f"Creatomate render failed: "
                                       f"{data.get('error_message', data)}")
                return RenderResult(
                    id=render_id, status=status,
                    url=data.get("url", ""),
                    duration=float(data.get("duration", 0) or 0),
                )
            # Creatomate doesn't expose a percentage on /renders/:id; we
            # approximate with elapsed time vs typical render time.
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
        with requests.get(url, stream=True, timeout=120) as r:
            r.raise_for_status()
            with dest.open("wb") as fh:
                for chunk in r.iter_content(chunk_size=1024 * 64):
                    if chunk:
                        fh.write(chunk)

    # -- composition ---------------------------------------------------
    def _build_source(self, req: RenderRequest) -> dict:
        elements: list[dict] = []

        # --- Background clips (track 1) ---
        for i, section in enumerate(req.sections):
            is_video = section.url.lower().endswith(
                (".mp4", ".mov", ".webm", ".m4v")
            )
            base: dict = {
                "type": "video" if is_video else "image",
                "source": section.url,
                "track": 1,
                "time": round(section.time, 3),
                "duration": round(section.duration, 3),
                "fit": section.fit,
                "volume": 0,
            }
            # Gentle Ken-Burns zoom on every section for that pro look.
            base["animations"] = [{
                "time": "start",
                "duration": round(section.duration, 3),
                "easing": "linear",
                "type": "scale",
                "scope": "element",
                "start_scale": "100%",
                "end_scale": "115%",
            }]
            # Crossfade between sections.
            if i > 0:
                base["animations"].append({
                    "time": "start", "duration": 0.35,
                    "type": "fade", "easing": "linear",
                })
            elements.append(base)

        # --- Voice-over (track 2) ---
        elements.append({
            "type": "audio",
            "source": req.voice_url,
            "track": 2,
            "time": 0,
        })

        # --- Background music (track 3) ---
        if req.music_url:
            elements.append({
                "type": "audio",
                "source": req.music_url,
                "track": 3,
                "time": 0,
                "duration": req.duration,
                "volume": "8%",
                "audio_fade_in": 0.6,
                "audio_fade_out": 1.0,
                "loop": True,
            })

        # --- Word-by-word animated captions (track 4) ---
        for cue in req.word_cues:
            word = cue.text.strip().upper()
            if not word:
                continue
            start = round(cue.start, 3)
            dur = max(0.12, round(cue.end - cue.start + 0.04, 3))
            is_keyword = any(ch for ch in word if ch.isdigit()) or len(word) >= 8
            fill = req.brand_color if is_keyword else req.text_color
            elements.append({
                "type": "text",
                "text": word,
                "track": 4,
                "time": start,
                "duration": dur,
                "x": "50%",
                "y": "72%",
                "width": "88%",
                "height": "20%",
                "x_alignment": "50%",
                "y_alignment": "50%",
                "font_family": req.font_family,
                "font_weight": "900",
                "font_size": "10 vmin",
                "fill_color": fill,
                "stroke_color": "#000000",
                "stroke_width": "0.5 vmin",
                "shadow_color": "rgba(0,0,0,0.9)",
                "shadow_blur": "1 vmin",
                "shadow_y": "0.3 vmin",
                "animations": [
                    {
                        "time": "start",
                        "duration": 0.18,
                        "easing": "back-out",
                        "type": "scale",
                        "start_scale": "55%",
                        "end_scale": "100%",
                    }
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
