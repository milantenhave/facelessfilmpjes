"""Fetch matching stock footage/images for each sentence of a script.

Supports Pexels + Pixabay. Falls back to generating a solid-colour still so the
video can always render end-to-end.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
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
}


def extract_keywords(text: str, extra: list[str] | None = None,
                     limit: int = 3) -> list[str]:
    """Pick 1–N strong keywords from a sentence."""
    tokens = re.findall(r"[a-zA-Z][a-zA-Z\-]{2,}", (text or "").lower())
    ranked = [t for t in tokens if t not in _STOPWORDS]
    if extra:
        ranked = [*extra, *ranked]
    # Stable dedupe
    seen: set[str] = set()
    out: list[str] = []
    for t in ranked:
        if t in seen:
            continue
        seen.add(t)
        out.append(t)
        if len(out) >= limit:
            break
    return out or ["abstract"]


@dataclass
class MediaClip:
    path: Path
    kind: str      # "video" | "image" | "color"
    duration: float | None = None
    source: str = ""
    query: str = ""


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

    def fetch_for_sentence(self, sentence: str, seed_keywords: list[str],
                           out_dir: Path, index: int) -> MediaClip:
        out_dir.mkdir(parents=True, exist_ok=True)
        keywords = extract_keywords(sentence, extra=seed_keywords, limit=3)
        query = " ".join(keywords[:2])

        for provider in self.providers:
            try:
                if provider == "pexels" and self.pexels_key:
                    clip = self._pexels(query, out_dir, index)
                    if clip:
                        return clip
                elif provider == "pixabay" and self.pixabay_key:
                    clip = self._pixabay(query, out_dir, index)
                    if clip:
                        return clip
            except Exception as exc:   # noqa: BLE001
                log.warning("media provider %s failed (%s) — trying next.",
                            provider, exc)

        log.info("no remote media for %r — using colour fallback.", query)
        return self._color_fallback(out_dir, index, query)

    # -- providers ------------------------------------------------------
    @retry(stop=stop_after_attempt(2),
           wait=wait_exponential(multiplier=1, min=1, max=4))
    def _pexels(self, query: str, out_dir: Path, index: int) -> MediaClip | None:
        cache_key = self.cache.key("pexels", query, self.orientation)
        meta_path = self.cache.path("media_meta", cache_key, ".json")
        resp = requests.get(
            "https://api.pexels.com/videos/search",
            headers={"Authorization": self.pexels_key},
            params={"query": query, "orientation": self.orientation,
                    "per_page": 8, "size": "medium"},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        videos = data.get("videos", [])
        if not videos:
            return None

        video = videos[0]
        files = sorted(
            (f for f in video.get("video_files", [])
             if f.get("file_type", "").startswith("video/")),
            key=lambda f: (f.get("height") or 0),
        )
        if not files:
            return None
        target = next((f for f in files if (f.get("height") or 0) >= 1080),
                      files[-1])
        url = target["link"]
        target_path = out_dir / f"clip_{index:02d}_pexels.mp4"
        self._download(url, target_path)
        meta_path.write_text(str(video.get("id")), "utf-8")
        return MediaClip(
            path=target_path, kind="video",
            duration=float(video.get("duration") or self.min_duration),
            source="pexels", query=query,
        )

    @retry(stop=stop_after_attempt(2),
           wait=wait_exponential(multiplier=1, min=1, max=4))
    def _pixabay(self, query: str, out_dir: Path, index: int) -> MediaClip | None:
        resp = requests.get(
            "https://pixabay.com/api/videos/",
            params={"key": self.pixabay_key, "q": query,
                    "per_page": 8, "safesearch": "true"},
            timeout=30,
        )
        resp.raise_for_status()
        hits = resp.json().get("hits", [])
        if not hits:
            return None
        video = hits[0]
        variants = video.get("videos", {})
        chosen = variants.get("large") or variants.get("medium") \
            or variants.get("small") or variants.get("tiny")
        if not chosen or not chosen.get("url"):
            return None
        target_path = out_dir / f"clip_{index:02d}_pixabay.mp4"
        self._download(chosen["url"], target_path)
        return MediaClip(
            path=target_path, kind="video",
            duration=float(video.get("duration") or self.min_duration),
            source="pixabay", query=query,
        )

    # -- fallback -------------------------------------------------------
    def _color_fallback(self, out_dir: Path, index: int, query: str) -> MediaClip:
        from PIL import Image
        target_path = out_dir / f"clip_{index:02d}_fallback.png"
        img = Image.new("RGB", (1080, 1920), self.fallback_color)
        img.save(target_path, "PNG")
        return MediaClip(path=target_path, kind="image",
                         duration=None, source="fallback", query=query)

    # -- io -------------------------------------------------------------
    @staticmethod
    def _download(url: str, dest: Path) -> None:
        with requests.get(url, stream=True, timeout=120) as r:
            r.raise_for_status()
            with dest.open("wb") as fh:
                for chunk in r.iter_content(chunk_size=1024 * 64):
                    if chunk:
                        fh.write(chunk)
