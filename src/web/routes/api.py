"""JSON API endpoints used by HTMX + the frontend for live data."""
from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse

from ...db import Channel, Job, JobStatus, session_scope
from ...voice_generator import TTSFactory

router = APIRouter()


@router.get("/jobs")
async def list_jobs(limit: int = 50):
    with session_scope() as s:
        rows = s.execute(
            select(Job).order_by(Job.created_at.desc()).limit(limit)
        ).scalars().all()
        return [
            {
                "id": j.id,
                "channel_id": j.channel_id,
                "status": j.status.value if isinstance(j.status, JobStatus)
                    else str(j.status),
                "progress": j.progress_pct,
                "detail": j.status_detail,
                "hook": (j.idea or {}).get("hook", ""),
                "video_url": j.platform_video_url,
                "error": j.error,
                "created_at": j.created_at.isoformat() if j.created_at else "",
            }
            for j in rows
        ]


@router.get("/jobs/{job_id}")
async def get_job(job_id: int):
    with session_scope() as s:
        j = s.get(Job, job_id)
        if not j:
            raise HTTPException(404, "job not found")
        return {
            "id": j.id,
            "channel_id": j.channel_id,
            "status": j.status.value if isinstance(j.status, JobStatus)
                else str(j.status),
            "progress": j.progress_pct,
            "detail": j.status_detail,
            "idea": j.idea,
            "script": j.script,
            "caption": j.caption,
            "video_url": j.platform_video_url,
            "video_path": j.video_path,
            "error": j.error,
        }


@router.post("/channels/{channel_id}/run")
async def run_channel(channel_id: int, request: Request):
    with session_scope() as s:
        channel = s.get(Channel, channel_id)
        if not channel:
            raise HTTPException(404, "channel not found")
    job_id = request.app.state.scheduler.enqueue_now(channel_id)
    return {"job_id": job_id}


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: int):
    with session_scope() as s:
        j = s.get(Job, job_id)
        if not j:
            raise HTTPException(404)
        if j.status == JobStatus.pending:
            j.status = JobStatus.cancelled
            return {"cancelled": True}
    return {"cancelled": False, "reason": "already running"}


@router.post("/jobs/{job_id}/delete")
async def delete_job(job_id: int):
    """Remove a job row; does not delete files on disk."""
    with session_scope() as s:
        j = s.get(Job, job_id)
        if not j:
            raise HTTPException(404)
        s.delete(j)
    return {"deleted": True}


@router.get("/voice/sample")
async def voice_sample(
    request: Request,
    voice: str = Query("nova"),
    text: str = Query("This is what the voice sounds like."),
):
    """Synthesize a 1-shot sample so you can audition voices in the UI."""
    cfg = request.app.state.cfg
    tts = TTSFactory(cfg)
    tmp = Path(tempfile.mkstemp(suffix=".mp3")[1])
    try:
        clip = tts.synthesize(text, tmp, voice_override=voice)
    except Exception as exc:   # noqa: BLE001
        raise HTTPException(500, f"voice sample failed: {exc}")
    return FileResponse(str(clip.path), media_type="audio/mpeg",
                        filename=f"sample-{voice}.mp3")
