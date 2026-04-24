"""OpenAI image generation (DALL-E 3) for per-sentence AI visuals.

Each sentence becomes a cinematic 9:16 still. Cost is ~$0.04 per standard
1024x1792 image, so a 25s video with 5 sentences = ~$0.20. Enable per-channel
via the `visual_mode` setting.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from ..utils.logger import get_logger

log = get_logger(__name__)


class OpenAIImages:
    API = "https://api.openai.com/v1/images/generations"
    DEFAULT_MODEL = "dall-e-3"

    def __init__(self, model: Optional[str] = None,
                 size: str = "1024x1792",
                 quality: str = "standard",
                 style_preset: str = (
                     "cinematic photorealistic, dramatic lighting, "
                     "shallow depth of field, 35mm film grain, "
                     "centred composition, vertical 9:16"
                 )) -> None:
        self.api_key = _clean(os.getenv("OPENAI_API_KEY", ""))
        self.model = _clean(model or os.getenv("OPENAI_IMAGE_MODEL") or "") \
            or self.DEFAULT_MODEL
        self.size = size
        self.quality = quality
        self.style_preset = style_preset

    def available(self) -> bool:
        return bool(self.api_key)

    @retry(stop=stop_after_attempt(3),
           wait=wait_exponential(multiplier=1, min=2, max=15))
    def generate(self, prompt: str, out_path: Path,
                 style_override: Optional[str] = None) -> Path:
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY missing for image generation.")

        preset = style_override or self.style_preset
        full_prompt = (
            f"{preset}. Visualise concretely: {prompt.strip()}. "
            "No text, no captions, no watermarks, no letters."
        )

        resp = requests.post(
            self.API,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.model,
                "prompt": full_prompt[:3800],
                "size": self.size,
                "quality": self.quality,
                "n": 1,
            },
            timeout=120,
        )
        if resp.status_code >= 400:
            raise RuntimeError(
                f"OpenAI image error {resp.status_code}: {resp.text[:400]}")
        data = resp.json().get("data") or []
        if not data:
            raise RuntimeError("OpenAI image API returned no images")

        image_url = data[0].get("url")
        b64 = data[0].get("b64_json")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if b64:
            import base64
            out_path.write_bytes(base64.b64decode(b64))
        elif image_url:
            dl = requests.get(image_url, timeout=120)
            dl.raise_for_status()
            out_path.write_bytes(dl.content)
        else:
            raise RuntimeError("no image payload in response")
        log.info("generated image %s (%s, %s)",
                 out_path, self.model, self.size)
        return out_path


def _clean(value: str) -> str:
    v = (value or "").strip().strip('"').strip("'")
    if "#" in v:
        v = v.split("#", 1)[0].strip()
    return v
