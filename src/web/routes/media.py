"""Serve media files.

- `/media/tmp/<token>`   — open, short-lived file handles for cloud renderers
- `/media/video/<id>`    — auth-protected preview of a finished job's MP4
"""
from __future__ import annotations

import mimetypes
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from ...db import Job, session_scope
from ...worker.runner import resolve_temp_file

router = APIRouter()


@router.get("/tmp/{token}")
async def get_tmp(token: str):
    path = resolve_temp_file(token)
    if not path or not Path(path).exists():
        raise HTTPException(404, "not found")
    mime, _ = mimetypes.guess_type(str(path))
    return FileResponse(str(path), media_type=mime or "application/octet-stream")


@router.get("/video/{job_id}")
async def get_video(job_id: int):
    """Stream the rendered MP4 for a job (auth required via middleware)."""
    with session_scope() as s:
        job = s.get(Job, job_id)
        if not job or not job.video_path:
            raise HTTPException(404, "video not available")
        path = Path(job.video_path)
    if not path.exists():
        raise HTTPException(404, "video file missing on disk")
    return FileResponse(str(path), media_type="video/mp4",
                        filename=f"faceless_{job_id:04d}.mp4")
