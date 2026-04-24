"""FastAPI app factory."""
from __future__ import annotations

import asyncio
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from ..db import init_db
from ..events import bus
from ..utils import load_config
from ..utils.logger import get_logger
from ..worker import WorkerScheduler
from .routes import api, media, oauth, pages, sse
from .security import requires_auth

log = get_logger(__name__)

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def create_app(cfg: dict | None = None) -> FastAPI:
    cfg = cfg or load_config()
    init_db()

    scheduler = WorkerScheduler()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        bus.bind_loop(asyncio.get_running_loop())
        scheduler.start()
        try:
            yield
        finally:
            scheduler.stop()

    app = FastAPI(title="facelessfilmpjes", lifespan=lifespan)
    app.state.cfg = cfg
    app.state.templates = TEMPLATES
    app.state.scheduler = scheduler

    # Middleware order matters: the LAST-registered runs OUTERMOST.
    # We want Session → Auth → App, so register Auth first, Session second.
    @app.middleware("http")
    async def auth_mw(request: Request, call_next):
        return await requires_auth(request, call_next)

    app.add_middleware(
        SessionMiddleware,
        secret_key=os.getenv("SESSION_SECRET", secrets.token_urlsafe(32)),
        session_cookie="faceless_session",
        max_age=60 * 60 * 24 * 30,
    )

    static_dir = BASE_DIR / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)),
                  name="static")

    # Routes
    app.include_router(pages.router)
    app.include_router(api.router, prefix="/api")
    app.include_router(oauth.router, prefix="/oauth")
    app.include_router(media.router, prefix="/media")
    app.include_router(sse.router)

    @app.get("/healthz")
    async def healthz():
        return {"ok": True}

    @app.get("/")
    async def root(request: Request):
        if not request.session.get("user"):
            return RedirectResponse("/login", status_code=303)
        return RedirectResponse("/dashboard", status_code=303)

    return app
