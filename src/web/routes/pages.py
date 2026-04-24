"""Server-rendered HTML pages (HTMX-friendly)."""
from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ...db import (
    Channel, Job, JobStatus, Niche, Platform, Schedule,
    session_scope,
)
from ..security import check_password

router = APIRouter()


def _render(request: Request, name: str, ctx: dict, status_code: int = 200):
    """Render a template using the Starlette (>=0.37) signature."""
    templates = request.app.state.templates
    return templates.TemplateResponse(request, name, ctx,
                                      status_code=status_code)


# -- auth -----------------------------------------------------------------
@router.get("/login", response_class=HTMLResponse)
async def login_get(request: Request, next: str = "/dashboard"):
    return _render(request, "login.html", {"next": next, "error": None})


@router.post("/login", response_class=HTMLResponse)
async def login_post(request: Request,
                     password: str = Form(...),
                     next: str = Form("/dashboard")):
    if not check_password(password):
        return _render(request, "login.html",
                       {"next": next, "error": "Wrong password."},
                       status_code=401)
    request.session["user"] = "admin"
    return RedirectResponse(next or "/dashboard", status_code=303)


@router.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


# -- pages ----------------------------------------------------------------
@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    with session_scope() as s:
        channels = s.execute(
            select(Channel).options(selectinload(Channel.niche),
                                     selectinload(Channel.tokens))
        ).scalars().all()
        recent_jobs = s.execute(
            select(Job).order_by(Job.created_at.desc()).limit(10)
        ).scalars().all()
        running_jobs = s.execute(
            select(Job).where(Job.status.notin_(
                [JobStatus.done, JobStatus.failed, JobStatus.cancelled]
            )).order_by(Job.created_at.asc())
        ).scalars().all()
        ctx = {
            "channels": [_channel_row(c) for c in channels],
            "recent_jobs": [_job_row(j) for j in recent_jobs],
            "running_jobs": [_job_row(j) for j in running_jobs],
        }
    return _render(request, "dashboard.html", ctx)


@router.get("/channels", response_class=HTMLResponse)
async def channels_list(request: Request):
    with session_scope() as s:
        channels = s.execute(
            select(Channel).options(selectinload(Channel.niche),
                                     selectinload(Channel.tokens),
                                     selectinload(Channel.schedules))
        ).scalars().all()
        niches = s.execute(select(Niche)).scalars().all()
        ctx = {
            "channels": [_channel_full(c) for c in channels],
            "niches": [{"id": n.id, "name": n.name} for n in niches],
            "platforms": [p.value for p in Platform],
        }
    return _render(request, "channels.html", ctx)


@router.post("/channels", response_class=HTMLResponse)
async def channels_create(
    request: Request,
    name: str = Form(...),
    platform: str = Form(...),
    niche_id: int = Form(...),
    voice_id: str = Form("nova"),
    accent_color: str = Form("#FFD400"),
    font_family: str = Form("Montserrat"),
    visual_mode: str = Form("stock"),
    image_style: str = Form(""),
):
    with session_scope() as s:
        c = Channel(
            name=name,
            platform=Platform(platform),
            niche_id=niche_id,
            style={
                "voice_id": voice_id,
                "accent_color": accent_color,
                "text_color": "#FFFFFF",
                "font_family": font_family,
                "visual_mode": visual_mode,
                "image_style": image_style.strip(),
            },
            upload_defaults={"privacy": "public", "made_for_kids": False},
        )
        s.add(c)
    return RedirectResponse("/channels", status_code=303)


@router.post("/channels/{channel_id}/delete")
async def channel_delete(channel_id: int, request: Request):
    with session_scope() as s:
        c = s.get(Channel, channel_id)
        if c:
            s.delete(c)
    request.app.state.scheduler.reload_schedules()
    return RedirectResponse("/channels", status_code=303)


@router.get("/channels/{channel_id}/edit", response_class=HTMLResponse)
async def channel_edit_get(request: Request, channel_id: int):
    with session_scope() as s:
        c = s.get(Channel, channel_id)
        if not c:
            return RedirectResponse("/channels", status_code=303)
        niches = s.execute(select(Niche)).scalars().all()
        ctx = {
            "channel": {
                "id": c.id, "name": c.name,
                "platform": c.platform.value if isinstance(c.platform, Platform)
                    else c.platform,
                "niche_id": c.niche_id,
                "style": c.style or {},
                "upload_defaults": c.upload_defaults or {},
                "active": c.active,
            },
            "niches": [{"id": n.id, "name": n.name} for n in niches],
            "platforms": [p.value for p in Platform],
        }
    return _render(request, "channel_edit.html", ctx)


@router.post("/channels/{channel_id}/edit")
async def channel_edit_post(
    channel_id: int,
    request: Request,
    name: str = Form(...),
    platform: str = Form(...),
    niche_id: int = Form(...),
    voice_id: str = Form("nova"),
    accent_color: str = Form("#FFD400"),
    font_family: str = Form("Montserrat"),
    visual_mode: str = Form("stock"),
    image_style: str = Form(""),
    privacy: str = Form("public"),
    active: bool = Form(True),
):
    with session_scope() as s:
        c = s.get(Channel, channel_id)
        if not c:
            return RedirectResponse("/channels", status_code=303)
        c.name = name
        c.platform = Platform(platform)
        c.niche_id = niche_id
        c.active = active
        style = dict(c.style or {})
        style.update({
            "voice_id": voice_id,
            "accent_color": accent_color,
            "text_color": "#FFFFFF",
            "font_family": font_family,
            "visual_mode": visual_mode,
            "image_style": image_style.strip(),
        })
        c.style = style
        ud = dict(c.upload_defaults or {})
        ud["privacy"] = privacy
        c.upload_defaults = ud
    return RedirectResponse("/channels", status_code=303)


@router.get("/niches", response_class=HTMLResponse)
async def niches_list(request: Request):
    with session_scope() as s:
        niches = s.execute(select(Niche)).scalars().all()
        rows = [{
            "id": n.id, "name": n.name, "tone": n.tone,
            "emotions": n.emotions or [],
            "language": n.language,
            "video_length_seconds": n.video_length_seconds,
            "description": n.description,
            "prompt_additions": n.prompt_additions,
        } for n in niches]
    return _render(request, "niches.html", {"niches": rows})


@router.post("/niches", response_class=HTMLResponse)
async def niches_create(
    request: Request,
    name: str = Form(...),
    tone: str = Form("neutral"),
    emotions: str = Form(""),
    language: str = Form("en"),
    video_length_seconds: int = Form(25),
    description: str = Form(""),
    prompt_additions: str = Form(""),
):
    emotion_list = [e.strip() for e in emotions.split(",") if e.strip()]
    with session_scope() as s:
        s.add(Niche(
            name=name, tone=tone, emotions=emotion_list,
            language=language, video_length_seconds=video_length_seconds,
            description=description, prompt_additions=prompt_additions,
        ))
    return RedirectResponse("/niches", status_code=303)


@router.post("/niches/{niche_id}/delete")
async def niche_delete(niche_id: int, request: Request):
    with session_scope() as s:
        n = s.get(Niche, niche_id)
        if n:
            s.delete(n)
    return RedirectResponse("/niches", status_code=303)


@router.get("/niches/{niche_id}/edit", response_class=HTMLResponse)
async def niche_edit_get(request: Request, niche_id: int):
    with session_scope() as s:
        n = s.get(Niche, niche_id)
        if not n:
            return RedirectResponse("/niches", status_code=303)
        ctx = {
            "niche": {
                "id": n.id, "name": n.name, "tone": n.tone,
                "emotions": ", ".join(n.emotions or []),
                "language": n.language,
                "video_length_seconds": n.video_length_seconds,
                "description": n.description or "",
                "prompt_additions": n.prompt_additions or "",
            }
        }
    return _render(request, "niche_edit.html", ctx)


@router.post("/niches/{niche_id}/edit")
async def niche_edit_post(
    niche_id: int,
    request: Request,
    name: str = Form(...),
    tone: str = Form("neutral"),
    emotions: str = Form(""),
    language: str = Form("en"),
    video_length_seconds: int = Form(25),
    description: str = Form(""),
    prompt_additions: str = Form(""),
):
    with session_scope() as s:
        n = s.get(Niche, niche_id)
        if not n:
            return RedirectResponse("/niches", status_code=303)
        n.name = name
        n.tone = tone
        n.emotions = [e.strip() for e in emotions.split(",") if e.strip()]
        n.language = language
        n.video_length_seconds = int(video_length_seconds)
        n.description = description
        n.prompt_additions = prompt_additions
    return RedirectResponse("/niches", status_code=303)


@router.get("/schedules", response_class=HTMLResponse)
async def schedules_list(request: Request):
    with session_scope() as s:
        rows = s.execute(
            select(Schedule).options(selectinload(Schedule.channel))
        ).scalars().all()
        channels = s.execute(select(Channel)).scalars().all()
        ctx = {
            "schedules": [{
                "id": r.id, "channel": r.channel.name if r.channel else "?",
                "channel_id": r.channel_id,
                "cron": r.cron, "videos_per_slot": r.videos_per_slot,
                "active": r.active,
                "last_run_at": r.last_run_at.isoformat() if r.last_run_at else "",
            } for r in rows],
            "channels": [{"id": c.id, "name": c.name} for c in channels],
        }
    return _render(request, "schedules.html", ctx)


@router.post("/schedules", response_class=HTMLResponse)
async def schedule_create(
    request: Request,
    channel_id: int = Form(...),
    cron: str = Form("0 9,15,20 * * *"),
    videos_per_slot: int = Form(1),
    active: bool = Form(True),
):
    with session_scope() as s:
        s.add(Schedule(channel_id=channel_id, cron=cron,
                       videos_per_slot=videos_per_slot, active=active))
    request.app.state.scheduler.reload_schedules()
    return RedirectResponse("/schedules", status_code=303)


@router.post("/schedules/{sched_id}/delete")
async def schedule_delete(sched_id: int, request: Request):
    with session_scope() as s:
        sc = s.get(Schedule, sched_id)
        if sc:
            s.delete(sc)
    request.app.state.scheduler.reload_schedules()
    return RedirectResponse("/schedules", status_code=303)


@router.post("/channels/{channel_id}/run-now")
async def channel_run_now(channel_id: int, request: Request):
    job_id = request.app.state.scheduler.enqueue_now(channel_id)
    return RedirectResponse(f"/jobs?highlight={job_id}", status_code=303)


@router.get("/jobs", response_class=HTMLResponse)
async def jobs_list(request: Request, highlight: int | None = None):
    with session_scope() as s:
        jobs = s.execute(
            select(Job).options(selectinload(Job.channel))
                       .order_by(Job.created_at.desc())
                       .limit(200)
        ).scalars().all()
        rows = [_job_row(j) for j in jobs]
    return _render(request, "jobs.html",
                   {"jobs": rows, "highlight": highlight})


# -- serializers ----------------------------------------------------------
def _channel_row(c: Channel) -> dict:
    return {
        "id": c.id, "name": c.name,
        "platform": c.platform.value if isinstance(c.platform, Platform)
            else c.platform,
        "niche": c.niche.name if c.niche else "—",
        "connected": any(t.platform == Platform.youtube
                         for t in (c.tokens or [])),
        "active": c.active,
    }


def _channel_full(c: Channel) -> dict:
    yt_tok = next((t for t in (c.tokens or [])
                   if t.platform == Platform.youtube), None)
    return {
        "id": c.id, "name": c.name,
        "platform": c.platform.value if isinstance(c.platform, Platform)
            else c.platform,
        "niche": c.niche.name if c.niche else "—",
        "niche_id": c.niche_id,
        "style": c.style or {},
        "upload_defaults": c.upload_defaults or {},
        "schedules": [{
            "id": s.id, "cron": s.cron,
            "videos_per_slot": s.videos_per_slot, "active": s.active,
        } for s in (c.schedules or [])],
        "youtube_connected": bool(yt_tok),
        "youtube_account": yt_tok.account_name if yt_tok else "",
    }


def _job_row(j: Job) -> dict:
    status = j.status.value if isinstance(j.status, JobStatus) else str(j.status)
    return {
        "id": j.id,
        "channel": j.channel.name if j.channel else "?",
        "status": status,
        "progress": round(j.progress_pct or 0, 1),
        "detail": j.status_detail,
        "created_at": j.created_at.isoformat() if j.created_at else "",
        "idea_hook": (j.idea or {}).get("hook", ""),
        "video_url": j.platform_video_url or "",
        "video_path": j.video_path,
        "error": j.error,
    }
