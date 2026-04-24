"""Compose final 9:16 videos with FFmpeg.

Strategy:
    1. For each sentence, take the matched MediaClip and normalise it to the
       requested resolution (crop + pad to 9:16, loop if shorter than needed).
    2. Concatenate the segments with optional fade transitions.
    3. Mix the voiceover (+ optional background music) underneath.
    4. Burn subtitles in via the FFmpeg `subtitles` filter for hard-coded SRT,
       styled through `force_style`.

The only hard dependency is an `ffmpeg` binary. `imageio_ffmpeg` vendors one if
the system doesn't have it installed.
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ..media_fetcher import MediaClip
from ..script_generator import Script
from ..subtitle_generator import SubtitleCue
from ..utils.logger import get_logger
from ..voice_generator import VoiceClip

log = get_logger(__name__)


def _ffmpeg_binary() -> str:
    binary = shutil.which("ffmpeg")
    if binary:
        return binary
    try:
        import imageio_ffmpeg   # type: ignore
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:   # noqa: BLE001
        raise RuntimeError(
            "ffmpeg is not available. Install it system-wide or `pip install "
            "imageio-ffmpeg`."
        ) from exc


def _run(cmd: list[str]) -> None:
    log.debug("ffmpeg: %s", " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        log.error("ffmpeg failed (%s):\n%s", proc.returncode, proc.stderr[-2000:])
        raise RuntimeError(f"ffmpeg failed: {proc.returncode}")


@dataclass
class _Segment:
    path: Path
    duration: float


class VideoEditor:
    def __init__(self, cfg: dict) -> None:
        v = cfg.get("video", {})
        self.width, self.height = v.get("resolution", [1080, 1920])
        self.fps = int(v.get("fps", 30))
        self.crf = int(v.get("crf", 20))
        self.transitions = v.get("transitions", "fade")
        self.zoom_pan = bool(v.get("zoom_pan", True))
        self.bg_music = v.get("background_music")
        self.music_volume = float(v.get("music_volume", 0.08))
        self.ffmpeg = _ffmpeg_binary()

        sub = cfg.get("subtitles", {})
        self.sub_cfg = sub
        self.sub_enabled = bool(sub.get("enabled", True))

    def build(
        self,
        script: Script,
        media: list[MediaClip],
        voice: VoiceClip,
        subtitles_path: Path,
        cues: list[SubtitleCue],
        work_dir: Path,
        out_path: Path,
    ) -> Path:
        work_dir.mkdir(parents=True, exist_ok=True)
        total_duration = voice.duration or max(3.0, len(script.full_text.split()) / 2.6)

        # Distribute duration proportional to sentence word count so cuts match speech.
        sentences = script.sentences or [script.full_text]
        counts = [max(1, len(s.split())) for s in sentences]
        total_words = sum(counts)
        spans = [total_duration * c / total_words for c in counts]

        if len(media) < len(spans):
            # Duplicate last clip to cover any missing media.
            while len(media) < len(spans):
                media.append(media[-1])

        segments: list[_Segment] = []
        for i, (clip, span) in enumerate(zip(media, spans)):
            seg_path = work_dir / f"seg_{i:02d}.mp4"
            self._normalise(clip, span, seg_path)
            segments.append(_Segment(seg_path, span))

        silent_video = work_dir / "silent.mp4"
        self._concat(segments, silent_video)

        out_path.parent.mkdir(parents=True, exist_ok=True)
        self._mux(silent_video, voice.path, subtitles_path if self.sub_enabled else None,
                  cues, out_path)
        return out_path

    # -- steps ----------------------------------------------------------
    def _normalise(self, clip: MediaClip, duration: float, out: Path) -> None:
        w, h = self.width, self.height
        vf = (
            f"scale={w}:{h}:force_original_aspect_ratio=increase,"
            f"crop={w}:{h},setsar=1,fps={self.fps}"
        )
        if clip.kind == "image":
            # Still image -> ken-burns-ish zoom via zoompan if enabled.
            if self.zoom_pan:
                frames = max(2, int(duration * self.fps))
                zoom = (
                    f"zoompan=z='min(zoom+0.0015,1.2)':d={frames}:"
                    f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
                    f"s={w}x{h}:fps={self.fps},setsar=1"
                )
                vf = zoom
            cmd = [
                self.ffmpeg, "-y", "-loop", "1", "-t", f"{duration:.3f}",
                "-i", str(clip.path),
                "-vf", vf,
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-preset", "veryfast", "-crf", str(self.crf),
                "-r", str(self.fps),
                str(out),
            ]
        else:
            cmd = [
                self.ffmpeg, "-y",
                "-stream_loop", "-1", "-t", f"{duration:.3f}",
                "-i", str(clip.path),
                "-an",
                "-vf", vf,
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-preset", "veryfast", "-crf", str(self.crf),
                "-r", str(self.fps),
                str(out),
            ]
        _run(cmd)

    def _concat(self, segments: list[_Segment], out: Path) -> None:
        if not segments:
            raise RuntimeError("no segments to concat")
        if self.transitions == "fade" and len(segments) > 1:
            self._concat_with_fade(segments, out)
        else:
            listfile = out.with_suffix(".txt")
            listfile.write_text(
                "\n".join(f"file '{s.path.as_posix()}'" for s in segments),
                "utf-8",
            )
            cmd = [
                self.ffmpeg, "-y", "-f", "concat", "-safe", "0",
                "-i", str(listfile),
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-preset", "veryfast", "-crf", str(self.crf),
                "-r", str(self.fps),
                str(out),
            ]
            _run(cmd)

    def _concat_with_fade(self, segments: list[_Segment], out: Path) -> None:
        fade = 0.35
        inputs: list[str] = []
        for s in segments:
            inputs.extend(["-i", str(s.path)])

        filter_parts: list[str] = []
        prev_label = "0:v"
        offset = max(0.0, segments[0].duration - fade)
        for i in range(1, len(segments)):
            next_label = f"v{i}"
            filter_parts.append(
                f"[{prev_label}][{i}:v]xfade=transition=fade:"
                f"duration={fade}:offset={offset:.3f}[{next_label}]"
            )
            prev_label = next_label
            offset += max(0.0, segments[i].duration - fade)

        if filter_parts:
            filter_complex = ";".join(filter_parts)
            cmd = [
                self.ffmpeg, "-y", *inputs,
                "-filter_complex", filter_complex,
                "-map", f"[{prev_label}]",
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-preset", "veryfast", "-crf", str(self.crf),
                "-r", str(self.fps),
                str(out),
            ]
            _run(cmd)
        else:
            shutil.copyfile(segments[0].path, out)

    def _mux(self, video_path: Path, voice_path: Path,
             subs_path: Path | None, cues: list[SubtitleCue],
             out: Path) -> None:
        inputs = [self.ffmpeg, "-y", "-i", str(video_path), "-i", str(voice_path)]
        filters: list[str] = []

        if subs_path and subs_path.exists() and cues:
            style = self._subtitle_style()
            subs_escaped = str(subs_path).replace(":", r"\:").replace(",", r"\,")
            filters.append(
                f"[0:v]subtitles='{subs_escaped}':force_style='{style}'[v]"
            )
            video_label = "[v]"
        else:
            video_label = "0:v"

        if self.bg_music and Path(self.bg_music).exists():
            inputs.extend(["-i", str(self.bg_music)])
            filters.append(
                f"[2:a]volume={self.music_volume}[bg];"
                f"[1:a][bg]amix=inputs=2:duration=first:dropout_transition=2[aout]"
            )
            audio_label = "[aout]"
        else:
            audio_label = "1:a"

        if filters:
            cmd = [*inputs, "-filter_complex", ";".join(filters),
                   "-map", video_label, "-map", audio_label]
        else:
            cmd = [*inputs, "-map", video_label, "-map", audio_label]

        cmd += [
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-preset", "veryfast", "-crf", str(self.crf),
            "-c:a", "aac", "-b:a", "192k",
            "-shortest",
            str(out),
        ]
        _run(cmd)

    # -- helpers --------------------------------------------------------
    def _subtitle_style(self) -> str:
        s = self.sub_cfg
        def hex_to_ass(color: str) -> str:
            color = color.lstrip("#")
            if len(color) != 6:
                color = "FFFFFF"
            # ASS is &HBBGGRR&
            r, g, b = color[0:2], color[2:4], color[4:6]
            return f"&H00{b}{g}{r}"

        return (
            f"FontName={s.get('font', 'DejaVuSans-Bold')},"
            f"FontSize={s.get('font_size', 72) // 2},"
            f"PrimaryColour={hex_to_ass(s.get('color', '#FFFFFF'))},"
            f"OutlineColour={hex_to_ass(s.get('stroke_color', '#000000'))},"
            f"BorderStyle=1,Outline={s.get('stroke_width', 6)},Shadow=0,"
            f"Alignment=5,MarginV=40,Bold=1"
        )
