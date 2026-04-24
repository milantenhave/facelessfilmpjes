"""Content-addressable cache on the filesystem.

Used to dedupe media downloads, LLM responses, TTS renders, etc.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


class Cache:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def key(*parts: Any) -> str:
        raw = "||".join(repr(p) for p in parts).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()[:24]

    def path(self, namespace: str, key: str, suffix: str = "") -> Path:
        bucket = self.root / namespace
        bucket.mkdir(parents=True, exist_ok=True)
        return bucket / f"{key}{suffix}"

    def has(self, namespace: str, key: str, suffix: str = "") -> bool:
        p = self.path(namespace, key, suffix)
        return p.exists() and p.stat().st_size > 0

    def read_json(self, namespace: str, key: str) -> Any | None:
        p = self.path(namespace, key, ".json")
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def write_json(self, namespace: str, key: str, value: Any) -> Path:
        p = self.path(namespace, key, ".json")
        p.write_text(json.dumps(value, ensure_ascii=False, indent=2), "utf-8")
        return p
