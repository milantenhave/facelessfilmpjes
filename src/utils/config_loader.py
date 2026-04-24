"""Load YAML config with sane defaults and env-var overrides."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

DEFAULT_CONFIG: dict[str, Any] = {
    "project": {
        "name": "facelessfilmpjes",
        "output_dir": "./videos",
        "cache_dir": "./.cache",
        "log_dir": "./logs",
    },
    "niches": [
        {"name": "self_improvement", "tone": "motivational",
         "emotions": ["curiosity", "motivation"], "weight": 1}
    ],
    "language": "en",
    "videos_per_run": 1,
    "ideas_per_niche": 5,
    "video_length_seconds": 25,
    "posting_frequency": "manual",
    "llm": {"provider": "template", "temperature": 0.9, "max_tokens": 800},
    "tts": {"engine": "pyttsx3", "voice": "default", "rate": 175, "speed": 1.0},
    "media": {"providers": ["pexels", "pixabay"], "per_sentence": 1,
              "orientation": "portrait", "min_duration_s": 3,
              "fallback_color": "#101010"},
    "video": {"resolution": [1080, 1920], "fps": 30, "crf": 20,
              "background_music": None, "music_volume": 0.08,
              "transitions": "fade", "zoom_pan": True},
    "subtitles": {"enabled": True, "mode": "word", "font": "DejaVuSans-Bold",
                  "font_size": 72, "color": "#FFFFFF", "stroke_color": "#000000",
                  "stroke_width": 6, "position": "center",
                  "highlight_color": "#FFD400"},
    "captions": {"hashtags_count": 12, "include_cta": True},
    "ab_testing": {"enabled": False, "variants_per_idea": 1},
    "uploader": {"dry_run": True, "platforms": ["tiktok", "instagram", "youtube"]},
    "analytics": {"db_path": "./analytics.db", "feedback_loop": True},
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    """Load .env + YAML config merged on top of :data:`DEFAULT_CONFIG`."""
    load_dotenv()

    candidates = [
        path,
        os.getenv("FACELESS_CONFIG"),
        "config/config.yaml",
        "config/config.example.yaml",
    ]

    user_cfg: dict[str, Any] = {}
    for candidate in candidates:
        if not candidate:
            continue
        p = Path(candidate)
        if p.exists():
            with p.open("r", encoding="utf-8") as fh:
                user_cfg = yaml.safe_load(fh) or {}
            break

    cfg = _deep_merge(DEFAULT_CONFIG, user_cfg)

    # env overrides
    if os.getenv("LLM_PROVIDER"):
        cfg["llm"]["provider"] = os.environ["LLM_PROVIDER"]
    if os.getenv("TTS_ENGINE"):
        cfg["tts"]["engine"] = os.environ["TTS_ENGINE"]
    if os.getenv("OUTPUT_DIR"):
        cfg["project"]["output_dir"] = os.environ["OUTPUT_DIR"]
    if os.getenv("CACHE_DIR"):
        cfg["project"]["cache_dir"] = os.environ["CACHE_DIR"]

    return cfg
