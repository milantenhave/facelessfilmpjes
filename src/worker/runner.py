"""Executes a single video job through the full pipeline.

Emits live status updates via the in-process event bus so the web UI can
stream them to the browser via Server-Sent Events.
"""
from __future__ import annotations

import os
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..caption_generator import CaptionGenerator
from ..db import Channel, Job, JobStatus, Niche, Platform, session_scope
from ..events import publish_job_update, publish_log
from ..idea_generator import Idea, IdeaGenerator
from ..llm import build_provider
from ..media_fetcher import MediaFetcher
from ..script_generator import Script, ScriptGenerator, split_sentences
from ..subtitle_generator import SubtitleCue, SubtitleGenerator
from ..uploader.youtube import YouTubeUploader
from ..utils import Cache, Paths, get_logger, load_config
from ..video_editor import (
    CreatomateRenderer, MediaSection, RenderRequest,
)
from ..voice_generator import TTSFactory

log = get_logger(__name__)


def _unwrap(exc: BaseException) -> str:
    """Surface the real error hiding behind tenacity.RetryError."""
    try:
        from tenacity import RetryError
        if isinstance(exc, RetryError) and exc.last_attempt is not None:
            inner = exc.last_attempt.exception()
            if inner is not None:
                return f"{type(inner).__name__}: {inner}"
    except Exception:
        pass
    return f"{type(exc).__name__}: {exc}"

# Registry of temporary served files (voice audio) that need to be reachable
# by Creatomate. The FastAPI /media/tmp/<token> route reads from this map.
_TEMP_FILES: dict[str, Path] = {}
_TEMP_LOCK = threading.Lock()


def register_temp_file(path: Path) -> str:
    token = secrets.token_urlsafe(24)
    with _TEMP_LOCK:
        _TEMP_FILES[token] = path
    return token


def resolve_temp_file(token: str) -> Optional[Path]:
    with _TEMP_LOCK:
        return _TEMP_FILES.get(token)


def public_media_url(token: str) -> str:
    base = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")
    return f"{base}/media/tmp/{token}"


@dataclass
class JobRunner:
    """Builds + uploads one video for one channel."""

    def __init__(self, cfg: Optional[dict] = None) -> None:
        self.cfg = cfg or load_config()
        self.paths = Paths.from_config(self.cfg)
        self.cache = Cache(self.paths.cache)
        self.llm = build_provider(self.cfg)
        self.media = MediaFetcher(self.cfg, self.cache)
        self.subs = SubtitleGenerator(self.cfg)
        self.tts = TTSFactory(self.cfg)
        self.renderer = CreatomateRenderer()
        self.youtube = YouTubeUploader()

    # ------------------------------------------------------------------
    def enqueue(self, channel_id: int, niche_id: Optional[int] = None) -> int:
        with session_scope() as s:
            job = Job(channel_id=channel_id, niche_id=niche_id,
                      status=JobStatus.pending)
            s.add(job); s.flush()
            return job.id

    def run(self, job_id: int) -> None:
        try:
            self._run_inner(job_id)
        except Exception as exc:   # noqa: BLE001
            msg = _unwrap(exc)
            log.exception("job %d failed: %s", job_id, msg)
            self._set_status(job_id, JobStatus.failed, 100.0, msg)
            publish_log(job_id, f"failed: {msg}", level="error")
            with session_scope() as s:
                j = s.get(Job, job_id)
                if j:
                    j.error = msg

    # ------------------------------------------------------------------
    def _run_inner(self, job_id: int) -> None:
        self._set_status(job_id, JobStatus.scripting, 5, "generating idea")

        with session_scope() as s:
            job = s.get(Job, job_id)
            if not job:
                raise RuntimeError(f"job {job_id} not found")
            channel = s.get(Channel, job.channel_id)
            niche = s.get(Niche, job.niche_id) if job.niche_id \
                else (channel.niche if channel else None)
            if not channel or not niche:
                raise RuntimeError("job is missing channel or niche")
            channel_id = channel.id
            platform = channel.platform
            style = dict(channel.style or {})
            upload_defaults = dict(channel.upload_defaults or {})
            niche_snapshot = {
                "id": niche.id, "name": niche.name, "tone": niche.tone,
                "emotions": list(niche.emotions or []),
                "language": niche.language,
                "video_length_seconds": niche.video_length_seconds,
                "prompt_additions": niche.prompt_additions or "",
            }

        # -- 1. Idea ----------------------------------------------------
        idea = self._generate_idea(niche_snapshot)
        publish_log(job_id, f"idea: {idea.hook}")

        # -- 2. Script --------------------------------------------------
        self._set_status(job_id, JobStatus.scripting, 15, "writing script")
        script_cfg = {**self.cfg, "video_length_seconds":
                      niche_snapshot["video_length_seconds"],
                      "language": niche_snapshot["language"]}
        script = ScriptGenerator(self.llm, script_cfg).run(idea)
        if not script or not script.full_text:
            raise RuntimeError("script generation produced empty output")

        with session_scope() as s:
            job = s.get(Job, job_id)
            job.idea = idea.to_dict()
            job.script = script.to_dict()

        # -- 3. TTS -----------------------------------------------------
        self._set_status(job_id, JobStatus.voicing, 30, "synthesising voice")
        slot = self.paths.video_slot(job_id)
        voice_ext = ".mp3" if os.getenv("OPENAI_API_KEY") else ".wav"
        voice_path = slot["audio"].with_suffix(voice_ext)
        voice_override = style.get("voice_id") or None
        try:
            voice = self.tts.synthesize(script.full_text, voice_path,
                                        voice_override=voice_override)
        except TypeError:
            voice = self.tts.synthesize(script.full_text, voice_path)

        # -- 4. Media ---------------------------------------------------
        self._set_status(job_id, JobStatus.fetching_media, 45,
                         "fetching stock footage")
        sentences = script.sentences or split_sentences(script.full_text)
        slot["media_dir"].mkdir(parents=True, exist_ok=True)
        seed_keywords = script.keywords or [idea.topic]
        media_clips = []
        for i, sentence in enumerate(sentences):
            clip = self.media.fetch_for_sentence(
                sentence, seed_keywords, slot["media_dir"], i)
            media_clips.append(clip)

        # -- 5. Subtitle cues ------------------------------------------
        self._set_status(job_id, JobStatus.fetching_media, 55,
                         "aligning subtitles")
        cues = self.subs.run(script, voice, slot["subs"])

        # -- 6. Render --------------------------------------------------
        self._set_status(job_id, JobStatus.rendering, 65,
                         "rendering with Creatomate")
        total_duration = voice.duration or sum(
            max(1.0, len(s.split()) / 2.6) for s in sentences
        )
        sections = self._sections_from_clips(media_clips, sentences, total_duration)
        voice_token = register_temp_file(voice_path)
        voice_url = public_media_url(voice_token)

        word_cues = [SubtitleCue(start=c.start, end=c.end, text=c.text)
                     for c in cues if c.text.strip()]

        request = RenderRequest(
            width=self.cfg["video"]["resolution"][0],
            height=self.cfg["video"]["resolution"][1],
            fps=self.cfg["video"].get("fps", 30),
            duration=total_duration,
            voice_url=voice_url,
            sections=sections,
            word_cues=word_cues,
            music_url=style.get("music_url") or self.cfg["video"].get("background_music"),
            brand_color=style.get("accent_color", "#FFD400"),
            text_color=style.get("text_color", "#FFFFFF"),
            font_family=style.get("font_family", "Montserrat"),
        )

        if self.renderer.available():
            def on_progress(pct: float) -> None:
                self._set_status(job_id, JobStatus.rendering,
                                 65 + pct * 0.2, f"rendering ({pct:.0f}%)")
            result = self.renderer.render(request, slot["video"],
                                          progress_cb=on_progress)
            with session_scope() as s:
                job = s.get(Job, job_id)
                job.render_id = result.id
                job.render_url = result.url
        else:
            publish_log(job_id,
                        "Creatomate key not set — falling back to local renderer.",
                        level="warn")
            from ..video_editor import VideoEditor
            VideoEditor(self.cfg).build(
                script=script, media=media_clips, voice=voice,
                subtitles_path=slot["subs"], cues=cues,
                work_dir=slot["media_dir"] / "_work",
                out_path=slot["video"],
            )

        # -- 7. Caption -------------------------------------------------
        self._set_status(job_id, JobStatus.uploading, 88, "writing caption")
        caption = CaptionGenerator(self.llm, self.cfg).run(script)
        with session_scope() as s:
            job = s.get(Job, job_id)
            job.caption = caption.to_dict()
            job.voice_path = str(voice_path)
            job.voice_duration = voice.duration
            job.video_path = str(slot["video"])

        # -- 8. Upload --------------------------------------------------
        if platform == Platform.youtube:
            self._set_status(job_id, JobStatus.uploading, 92,
                             "uploading to YouTube")
            privacy = upload_defaults.get("privacy", "public")
            made_for_kids = bool(upload_defaults.get("made_for_kids", False))

            def on_upload_progress(pct: float) -> None:
                self._set_status(job_id, JobStatus.uploading,
                                 92 + pct * 0.07,
                                 f"uploading ({pct:.0f}%)")

            up = self.youtube.upload(
                channel_id=channel_id,
                video_path=slot["video"],
                title=caption.title,
                description=caption.description,
                tags=caption.hashtags,
                privacy=privacy,
                made_for_kids=made_for_kids,
                progress_cb=on_upload_progress,
            )
            with session_scope() as s:
                job = s.get(Job, job_id)
                job.platform_video_id = up.video_id
                job.platform_video_url = up.video_url
        else:
            publish_log(job_id,
                        f"platform {platform.value} upload not yet implemented — "
                        "saving as dry-run.",
                        level="warn")

        # -- 9. Done ----------------------------------------------------
        self._set_status(job_id, JobStatus.done, 100, "done")
        with session_scope() as s:
            job = s.get(Job, job_id)
            job.finished_at = datetime.now(timezone.utc)

    # ------------------------------------------------------------------
    def _generate_idea(self, niche: dict) -> Idea:
        cfg_for_idea = {
            **self.cfg,
            "niches": [{
                "name": niche["name"], "tone": niche["tone"],
                "emotions": niche["emotions"], "weight": 1,
            }],
            "ideas_per_niche": 6,
            "language": niche["language"],
        }
        ideas = IdeaGenerator(self.llm, cfg_for_idea).run()
        if not ideas:
            raise RuntimeError("idea generator returned nothing")
        return ideas[0]

    def _sections_from_clips(self, media_clips, sentences,
                             total_duration: float) -> list[MediaSection]:
        counts = [max(1, len(s.split())) for s in sentences] or [1]
        total_words = sum(counts)
        spans = [total_duration * c / total_words for c in counts]
        if len(media_clips) < len(spans):
            while len(media_clips) < len(spans):
                media_clips.append(media_clips[-1])

        sections: list[MediaSection] = []
        cursor = 0.0
        for clip, span in zip(media_clips, spans):
            url = self._public_url_for_clip(clip)
            sections.append(MediaSection(url=url, time=cursor, duration=span))
            cursor += span
        return sections

    def _public_url_for_clip(self, clip) -> str:
        # Prefer the original remote URL (Pexels/Pixabay): it's already public.
        remote = getattr(clip, "remote_url", "")
        if remote:
            return remote
        # Otherwise serve the downloaded/fallback file via /media/tmp.
        token = register_temp_file(Path(clip.path))
        return public_media_url(token)

    def _set_status(self, job_id: int, status: JobStatus,
                    progress: float, detail: str) -> None:
        with session_scope() as s:
            job = s.get(Job, job_id)
            if not job:
                return
            job.status = status
            job.progress_pct = progress
            job.status_detail = detail
            channel_id = job.channel_id
        publish_job_update(job_id, channel_id, status.value if hasattr(status, "value") else str(status),
                           progress, detail)
