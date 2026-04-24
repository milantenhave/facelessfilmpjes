"""Smoke tests that run with the template LLM + silent TTS fallback.

These tests avoid external services and heavy dependencies; they verify the
module wiring is sound.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from src.caption_generator import CaptionGenerator
from src.idea_generator import IdeaGenerator
from src.llm.template_provider import TemplateProvider
from src.script_generator import ScriptGenerator
from src.subtitle_generator import SubtitleGenerator
from src.utils.cache import Cache
from src.utils.config_loader import DEFAULT_CONFIG
from src.voice_generator import VoiceGenerator


@pytest.fixture
def cfg(tmp_path: Path) -> dict:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy
    cfg["project"]["output_dir"] = str(tmp_path / "videos")
    cfg["project"]["cache_dir"] = str(tmp_path / "cache")
    cfg["project"]["log_dir"] = str(tmp_path / "logs")
    cfg["niches"] = [{"name": "facts", "tone": "playful",
                      "emotions": ["curiosity"], "weight": 1}]
    cfg["ideas_per_niche"] = 3
    cfg["video_length_seconds"] = 15
    cfg["tts"]["engine"] = "silence"
    cfg["analytics"]["db_path"] = str(tmp_path / "analytics.db")
    return cfg


def test_idea_generator(cfg):
    llm = TemplateProvider()
    ideas = IdeaGenerator(llm, cfg).run()
    assert len(ideas) >= 3
    assert all(i.hook for i in ideas)


def test_script_generator(cfg):
    llm = TemplateProvider()
    ideas = IdeaGenerator(llm, cfg).run()
    script = ScriptGenerator(llm, cfg).run(ideas[0])
    assert script is not None
    assert script.full_text
    assert script.sentences


def test_caption_generator(cfg):
    llm = TemplateProvider()
    idea = IdeaGenerator(llm, cfg).run()[0]
    script = ScriptGenerator(llm, cfg).run(idea)
    caption = CaptionGenerator(llm, cfg).run(script)
    assert caption.title
    assert caption.hashtags
    assert all(t.startswith("#") for t in caption.hashtags)


def test_voice_silence_fallback(cfg, tmp_path):
    cfg["tts"]["engine"] = "silence"
    vg = VoiceGenerator(cfg)
    clip = vg.synthesize("hello world this is a test", tmp_path / "out.wav")
    assert clip.path.exists()
    assert clip.duration > 0


def test_subtitles_even_align(cfg, tmp_path):
    llm = TemplateProvider()
    idea = IdeaGenerator(llm, cfg).run()[0]
    script = ScriptGenerator(llm, cfg).run(idea)
    voice = VoiceGenerator(cfg).synthesize(script.full_text, tmp_path / "a.wav")

    cues = SubtitleGenerator(cfg).run(script, voice, tmp_path / "subs.srt")
    assert cues
    assert (tmp_path / "subs.srt").read_text("utf-8").strip()


def test_cache_roundtrip(tmp_path):
    cache = Cache(tmp_path / "c")
    k = cache.key("a", 1)
    cache.write_json("ns", k, {"x": 1})
    assert cache.read_json("ns", k) == {"x": 1}


def test_pipeline_runs_end_to_end(cfg, tmp_path):
    # Only run the full pipeline if ffmpeg is available; otherwise skip.
    if not shutil.which("ffmpeg"):
        try:
            import imageio_ffmpeg  # noqa: F401
        except ImportError:
            pytest.skip("ffmpeg not available")

    # Force offline-everything
    cfg["llm"]["provider"] = "template"
    cfg["media"]["providers"] = []
    cfg["tts"]["engine"] = "silence"
    cfg["videos_per_run"] = 1
    cfg["video"]["resolution"] = [270, 480]  # tiny for speed
    cfg["video"]["fps"] = 15

    from src.pipeline import Pipeline
    results = Pipeline(cfg).run_once()
    assert results
    for r in results:
        assert r.video_path.exists()
        assert r.meta_path.exists()
        meta = json.loads(r.meta_path.read_text("utf-8"))
        assert meta["title"]
