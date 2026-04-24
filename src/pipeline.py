"""End-to-end pipeline that stitches all modules together."""
from __future__ import annotations

import hashlib
import json
import random
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from .analytics import Analytics, VideoRecord
from .caption_generator import CaptionGenerator
from .idea_generator import Idea, IdeaGenerator
from .llm import build_provider
from .media_fetcher import MediaFetcher
from .script_generator import Script, ScriptGenerator
from .subtitle_generator import SubtitleGenerator
from .uploader import Uploader
from .utils import Cache, Paths, get_logger, load_config
from .video_editor import VideoEditor
from .voice_generator import VoiceGenerator

log = get_logger(__name__)


@dataclass
class PipelineResult:
    video_path: Path
    meta_path: Path
    idea: Idea
    script: Script


class Pipeline:
    def __init__(self, cfg: dict | None = None) -> None:
        self.cfg = cfg or load_config()
        self.paths = Paths.from_config(self.cfg)
        self.cache = Cache(self.paths.cache)
        self.llm = build_provider(self.cfg)
        self.idea_gen = IdeaGenerator(self.llm, self.cfg)
        self.script_gen = ScriptGenerator(self.llm, self.cfg)
        self.caption_gen = CaptionGenerator(self.llm, self.cfg)
        self.voice_gen = VoiceGenerator(self.cfg)
        self.media = MediaFetcher(self.cfg, self.cache)
        self.subs = SubtitleGenerator(self.cfg)
        self.editor = VideoEditor(self.cfg)
        self.uploader = Uploader(self.cfg)
        self.analytics = Analytics(self.cfg)
        self._seen_hashes: set[str] = set()

    # -- entrypoints ----------------------------------------------------
    def run_once(self, override_n: int | None = None) -> list[PipelineResult]:
        want = override_n or int(self.cfg.get("videos_per_run", 1))
        patterns = self.analytics.winning_patterns()
        ideas = self.idea_gen.run(winning_patterns=patterns)
        if not ideas:
            log.warning("no ideas generated; aborting run.")
            return []

        ab_cfg = self.cfg.get("ab_testing", {})
        variants = max(1, int(ab_cfg.get("variants_per_idea", 1))) \
            if ab_cfg.get("enabled") else 1

        chosen: list[tuple[Idea, str]] = []
        for idea in ideas:
            if self._is_duplicate(idea):
                continue
            for i in range(variants):
                chosen.append((idea, chr(ord("A") + i)))
                if len(chosen) >= want:
                    break
            if len(chosen) >= want:
                break

        results: list[PipelineResult] = []
        # Light parallelism: script + media fetch + voice per slot in threads.
        with ThreadPoolExecutor(max_workers=min(4, len(chosen) or 1)) as pool:
            futures = {
                pool.submit(self._build_one, idea, variant, idx): idx
                for idx, (idea, variant) in enumerate(chosen, start=1)
            }
            for fut in as_completed(futures):
                try:
                    res = fut.result()
                    if res:
                        results.append(res)
                except Exception:   # noqa: BLE001
                    log.error("video slot failed:\n%s", traceback.format_exc())

        log.info("run_once finished: %d/%d videos produced",
                 len(results), len(chosen))
        return results

    # -- per-video work unit -------------------------------------------
    def _build_one(self, idea: Idea, variant: str, index: int) -> PipelineResult | None:
        slot = self.paths.video_slot(index)
        script = self.script_gen.run(idea, variant=variant)
        if not script or not script.full_text:
            log.warning("empty script for idea %r — skipping.", idea.hook)
            return None

        slot["script"].write_text(json.dumps(script.to_dict(), ensure_ascii=False,
                                             indent=2), "utf-8")

        voice = self.voice_gen.synthesize(script.full_text, slot["audio"])

        slot["media_dir"].mkdir(parents=True, exist_ok=True)
        media_clips = []
        seed_keywords = script.keywords or [script.idea.topic]
        for i, sentence in enumerate(script.sentences or [script.full_text]):
            clip = self.media.fetch_for_sentence(
                sentence, seed_keywords, slot["media_dir"], i,
            )
            media_clips.append(clip)

        cues = self.subs.run(script, voice, slot["subs"])

        work_dir = slot["media_dir"] / "_work"
        self.editor.build(
            script=script,
            media=media_clips,
            voice=voice,
            subtitles_path=slot["subs"],
            cues=cues,
            work_dir=work_dir,
            out_path=slot["video"],
        )

        caption = self.caption_gen.run(script)
        self.uploader.run(slot["video"], caption, script, slot["meta"])

        rec = VideoRecord(
            niche=script.idea.niche, topic=script.idea.topic,
            hook=script.hook, variant=script.variant, angle=script.idea.angle,
            video_path=str(slot["video"]), meta_path=str(slot["meta"]),
            duration=voice.duration,
        )
        rec.id = self.analytics.record_video(rec)

        log.info("built video %s (idea=%r, variant=%s)",
                 slot["video"], idea.hook, variant)
        return PipelineResult(slot["video"], slot["meta"], idea, script)

    # -- dedupe ---------------------------------------------------------
    def _is_duplicate(self, idea: Idea) -> bool:
        digest = hashlib.sha256(
            (idea.hook + "|" + idea.topic).lower().encode("utf-8")
        ).hexdigest()
        if digest in self._seen_hashes:
            return True
        self._seen_hashes.add(digest)
        return False
