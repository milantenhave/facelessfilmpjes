"""Fetch matching stock footage/images for each sentence of a script.

Supports Pexels + Pixabay. Designed for visual variety:
- Each sentence gets its own query, formed primarily from sentence tokens.
- We request 15 candidates per query and pick a random unused one, so repeat
  runs and repeat queries don't return the same clip.
- A per-batch memory of already-used video IDs prevents dupes within one video.
- If all providers are exhausted we fall back to a solid-colour still.
"""
from __future__ import annotations

import os
import random
import re
from dataclasses import dataclass, field
from pathlib import Path

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from ..utils.cache import Cache
from ..utils.logger import get_logger

log = get_logger(__name__)

_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "of", "for", "to", "in", "on", "with",
    "is", "are", "was", "were", "be", "been", "being", "it", "this", "that",
    "these", "those", "at", "as", "by", "from", "your", "you", "i", "we", "they",
    "them", "their", "our", "his", "her", "its", "my", "me", "will", "just",
    "so", "if", "then", "than", "not", "no", "yes", "do", "does", "did", "done",
    "what", "when", "why", "how", "who", "which", "because", "about", "into",
    "over", "under", "out", "up", "down", "very", "really", "also", "more",
    "most", "some", "any", "each", "every", "all", "one", "two", "three",
}


def extract_keywords(text: str, extra: list[str] | None = None,
                     limit: int = 3) -> list[str]:
    """Pick 1–N concrete keywords from a sentence.

    Sentence tokens come FIRST so each sentence gets its own visual. Shared
    topic seeds are only appended as backup if the sentence is too generic.
    """
    tokens = re.findall(r"[a-zA-Z][a-zA-Z\-]{2,}", (text or "").lower())
    ranked = [t for t in tokens if t not in _STOPWORDS]
    if extra:
        ranked = [*ranked, *[e.lower() for e in extra]]
    seen: set[str] = set()
    out: list[str] = []
    for t in ranked:
        if t in seen:
            continue
        seen.add(t)
        out.append(t)
        if len(out) >= limit:
            break
    return out or ["motion background"]


@dataclass
class MediaClip:
    path: Path
    kind: str      # "video" | "image" | "color"
    duration: float | None = None
    source: str = ""
    query: str = ""
    remote_url: str = ""      # direct CDN URL when available (cloud renderers)
    native_duration: float = 0.0
    trim_start: float = 0.0


@dataclass
class MediaBatch:
    """Per-video memory: avoids using the same stock clip twice in one render."""
    used_ids: set[str] = field(default_factory=set)
    used_urls: set[str] = field(default_factory=set)


class MediaFetcher:
    def __init__(self, cfg: dict, cache: Cache) -> None:
        self.cfg = cfg.get("media", {})
        self.cache = cache
        self.orientation = self.cfg.get("orientation", "portrait")
        self.min_duration = float(self.cfg.get("min_duration_s", 3))
        self.fallback_color = self.cfg.get("fallback_color", "#101010")
        self.providers = [p.lower() for p in self.cfg.get("providers", [])]
        self.pexels_key = os.getenv("PEXELS_API_KEY", "")
        self.pixabay_key = os.getenv("PIXABAY_API_KEY", "")

    def new_batch(self) -> MediaBatch:
        return MediaBatch()

    # ------------------------------------------------------------------
    def fetch_for_sentence(
        self,
        sentence: str,
        seed_keywords: list[str],
        out_dir: Path,
        index: int,
        batch: MediaBatch | None = None,
        *,
        visual_mode: str = "stock",
        image_style: str | None = None,
    ) -> MediaClip:
        """Fetch or generate a visual for one sentence.

        visual_mode:
            "stock"     — Pexels / Pixabay only (default, free)
            "ai_images" — OpenAI DALL-E per sentence (paid, most relevant)
            "mixed"     — alternate: AI for short sentences, stock otherwise
        """
        out_dir.mkdir(parents=True, exist_ok=True)
        batch = batch or MediaBatch()

        use_ai = visual_mode == "ai_images" or (
            visual_mode == "mixed" and (index % 2 == 0)
        )

        if use_ai:
            clip = self._openai_image(sentence, out_dir, index, image_style)
            if clip:
                return clip
            log.info("AI image unavailable for sentence %d — "
                     "falling back to stock.", index)

        return self._stock_for_sentence(sentence, seed_keywords, out_dir,
                                        index, batch)

    # ------------------------------------------------------------------
    def _stock_for_sentence(
        self,
        sentence: str,
        seed_keywords: list[str],
        out_dir: Path,
        index: int,
        batch: MediaBatch,
    ) -> MediaClip:
        primary_kw = extract_keywords(sentence, extra=None, limit=2)
        fallback_kw = [k.lower() for k in (seed_keywords or [])][:2] or \
                      ["cinematic", "lifestyle"]

        queries = [
            " ".join(primary_kw),
            primary_kw[0] if primary_kw else "motion",
            " ".join(fallback_kw),
            random.choice(fallback_kw),
            "cinematic abstract",
        ]
        queries = list(dict.fromkeys(q for q in queries if q))

        for query in queries:
            for provider in self.providers:
                try:
                    clip = None
                    if provider == "pexels" and self.pexels_key:
                        clip = self._pexels(query, out_dir, index, batch)
                    elif provider == "pixabay" and self.pixabay_key:
                        clip = self._pixabay(query, out_dir, index, batch)
                    if clip:
                        return clip
                except Exception as exc:   # noqa: BLE001
                    log.warning("media provider %s failed for %r (%s) — "
                                "trying next.", provider, query, exc)

        log.info("no remote media match — using colour fallback for sentence %d.",
                 index)
        return self._color_fallback(out_dir, index, " ".join(primary_kw))

    def _openai_image(self, sentence: str, out_dir: Path, index: int,
                      style_override: str | None) -> MediaClip | None:
        try:
            from .openai_images import OpenAIImages
        except Exception:   # noqa: BLE001
            return None
        provider = OpenAIImages()
        if not provider.available():
            return None
        try:
            target_path = out_dir / f"clip_{index:02d}_ai.png"
            provider.generate(sentence, target_path,
                              style_override=style_override)
        except Exception as exc:  # noqa: BLE001
            log.warning("AI image generation failed (%s) — falling back.", exc)
            return None
        return MediaClip(
            path=target_path, kind="image",
            duration=None, source="openai_image",
            query=sentence[:80],
        )

    # ------------------------------------------------------------------ providers
    @retry(stop=stop_after_attempt(2),
           wait=wait_exponential(multiplier=1, min=1, max=4))
    def _pexels(self, query: str, out_dir: Path, index: int,
                batch: MediaBatch) -> MediaClip | None:
        resp = requests.get(
            "https://api.pexels.com/videos/search",
            headers={"Authorization": self.pexels_key},
            params={
                "query": query,
                "orientation": self.orientation,
                "per_page": 15,
                "size": "medium",
            },
            timeout=30,
        )
        resp.raise_for_status()
        videos = resp.json().get("videos", []) or []
        # Keep only unused ones, then pick randomly from the top.
        candidates = [v for v in videos
                      if str(v.get("id")) not in batch.used_ids]
        if not candidates:
            return None
        random.shuffle(candidates)
        video = candidates[0]

        files = sorted(
            (f for f in video.get("video_files", [])
             if str(f.get("file_type", "")).startswith("video/")),
            key=lambda f: (f.get("height") or 0),
        )
        if not files:
            return None
        # Prefer 1080p portrait if available
        target = next((f for f in files
                       if (f.get("height") or 0) >= 1080
                       and (f.get("height") or 0) >= (f.get("width") or 0)),
                      None) or next(
            (f for f in files if (f.get("height") or 0) >= 1080), None,
        ) or files[-1]
        url = target["link"]
        if url in batch.used_urls:
            return None

        native_duration = float(video.get("duration") or self.min_duration)
        batch.used_ids.add(str(video.get("id")))
        batch.used_urls.add(url)

        target_path = out_dir / f"clip_{index:02d}_pexels.mp4"
        self._download(url, target_path)
        return MediaClip(
            path=target_path, kind="video",
            duration=native_duration, source="pexels",
            query=query, remote_url=url,
            native_duration=native_duration,
        )

    @retry(stop=stop_after_attempt(2),
           wait=wait_exponential(multiplier=1, min=1, max=4))
    def _pixabay(self, query: str, out_dir: Path, index: int,
                 batch: MediaBatch) -> MediaClip | None:
        resp = requests.get(
            "https://pixabay.com/api/videos/",
            params={"key": self.pixabay_key, "q": query,
                    "per_page": 15, "safesearch": "true",
                    "orientation": "vertical"},
            timeout=30,
        )
        resp.raise_for_status()
        hits = resp.json().get("hits", []) or []
        candidates = [h for h in hits
                      if str(h.get("id")) not in batch.used_ids]
        if not candidates:
            return None
        random.shuffle(candidates)
        video = candidates[0]

        variants = video.get("videos", {})
        chosen = variants.get("large") or variants.get("medium") \
            or variants.get("small") or variants.get("tiny")
        if not chosen or not chosen.get("url"):
            return None
        url = chosen["url"]
        if url in batch.used_urls:
            return None

        native_duration = float(video.get("duration") or self.min_duration)
        batch.used_ids.add(str(video.get("id")))
        batch.used_urls.add(url)

        target_path = out_dir / f"clip_{index:02d}_pixabay.mp4"
        self._download(url, target_path)
        return MediaClip(
            path=target_path, kind="video",
            duration=native_duration, source="pixabay",
            query=query, remote_url=url,
            native_duration=native_duration,
        )

    # ------------------------------------------------------------------ fallback
    def _color_fallback(self, out_dir: Path, index: int,
                        query: str) -> MediaClip:
        from PIL import Image
        target_path = out_dir / f"clip_{index:02d}_fallback.png"
        img = Image.new("RGB", (1080, 1920), self.fallback_color)
        img.save(target_path, "PNG")
        return MediaClip(path=target_path, kind="image",
                         duration=None, source="fallback", query=query)

    # ------------------------------------------------------------------ io
    @staticmethod
    def _download(url: str, dest: Path) -> None:
        with requests.get(url, stream=True, timeout=120) as r:
            r.raise_for_status()
            with dest.open("wb") as fh:
                for chunk in r.iter_content(chunk_size=1024 * 64):
                    if chunk:
                        fh.write(chunk)
