"""Prepare upload-ready bundles per platform.

By default this is a dry-run: it writes a metadata JSON next to the video so a
human (or a downstream automation) can publish. Real integrations for TikTok,
Instagram and YouTube should be implemented by subclassing `PlatformHook`.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ..caption_generator import Caption
from ..script_generator import Script
from ..utils.logger import get_logger

log = get_logger(__name__)


@dataclass
class UploadResult:
    platform: str
    status: str
    detail: str = ""


class PlatformHook:
    name: str = "base"

    def prepare(self, *, video: Path, caption: Caption, script: Script,
                meta: dict) -> UploadResult:
        return UploadResult(self.name, "prepared",
                            f"Ready at {video} (dry-run).")


class TikTokHook(PlatformHook):
    name = "tiktok"


class InstagramHook(PlatformHook):
    name = "instagram"


class YouTubeHook(PlatformHook):
    name = "youtube"


_HOOKS: dict[str, type[PlatformHook]] = {
    "tiktok": TikTokHook,
    "instagram": InstagramHook,
    "youtube": YouTubeHook,
}


class Uploader:
    def __init__(self, cfg: dict) -> None:
        up = cfg.get("uploader", {})
        self.dry_run = bool(up.get("dry_run", True))
        self.platforms = [p.lower() for p in up.get("platforms", [])]

    def run(self, video: Path, caption: Caption, script: Script,
            meta_path: Path) -> list[UploadResult]:
        meta = {
            "video": str(video),
            "title": caption.title,
            "description": caption.description,
            "hashtags": caption.hashtags,
            "script": script.to_dict(),
            "dry_run": self.dry_run,
            "platforms": self.platforms,
        }
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), "utf-8")

        results: list[UploadResult] = []
        for p in self.platforms:
            hook_cls = _HOOKS.get(p)
            if not hook_cls:
                results.append(UploadResult(p, "skipped", "unknown platform"))
                continue
            hook = hook_cls()
            if self.dry_run:
                results.append(UploadResult(hook.name, "prepared",
                                            f"dry-run; metadata at {meta_path}"))
                continue
            try:
                results.append(hook.prepare(video=video, caption=caption,
                                            script=script, meta=meta))
            except Exception as exc:   # noqa: BLE001
                log.exception("upload hook %s failed", hook.name)
                results.append(UploadResult(hook.name, "failed", str(exc)))
        return results
