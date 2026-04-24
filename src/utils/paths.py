"""Filesystem layout helpers."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class Paths:
    root: Path
    output: Path
    cache: Path
    logs: Path

    @classmethod
    def from_config(cls, cfg: dict) -> "Paths":
        project = cfg.get("project", {})
        root = Path(project.get("root", ".")).resolve()
        output = Path(project.get("output_dir", "./videos")).resolve()
        cache = Path(project.get("cache_dir", "./.cache")).resolve()
        logs = Path(project.get("log_dir", "./logs")).resolve()
        for p in (output, cache, logs):
            p.mkdir(parents=True, exist_ok=True)
        return cls(root=root, output=output, cache=cache, logs=logs)

    def today_dir(self) -> Path:
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        d = self.output / stamp
        (d / "audio").mkdir(parents=True, exist_ok=True)
        (d / "scripts").mkdir(parents=True, exist_ok=True)
        (d / "media").mkdir(parents=True, exist_ok=True)
        (d / "subtitles").mkdir(parents=True, exist_ok=True)
        return d

    def video_slot(self, index: int) -> dict[str, Path]:
        day = self.today_dir()
        slug = f"video_{index:02d}"
        return {
            "video": day / f"{slug}.mp4",
            "meta": day / f"{slug}.json",
            "audio": day / "audio" / f"{slug}.wav",
            "script": day / "scripts" / f"{slug}.txt",
            "subs": day / "subtitles" / f"{slug}.srt",
            "media_dir": day / "media" / slug,
        }
