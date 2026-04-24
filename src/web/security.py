"""Simple password-based auth for the dashboard.

The dashboard holds OAuth tokens that can post to your social accounts — do
not expose it publicly without auth. Set DASHBOARD_PASSWORD in `.env`.
"""
from __future__ import annotations

import hmac
import os

from fastapi import Request
from fastapi.responses import RedirectResponse

OPEN_PATHS = {
    "/login", "/logout", "/healthz", "/favicon.ico",
    "/static", "/oauth",
    # Only `/media/tmp/<token>` is open so Creatomate can fetch voice-overs
    # with an unguessable token. Finished-video previews live under
    # `/media/video/...` and stay behind the login.
    "/media/tmp",
}


async def requires_auth(request: Request, call_next):
    password = os.getenv("DASHBOARD_PASSWORD", "")
    if not password:
        # Auth disabled if no password is set (dev only).
        return await call_next(request)

    path = request.url.path
    if any(path == p or path.startswith(p + "/") for p in OPEN_PATHS):
        return await call_next(request)

    if request.session.get("user"):
        return await call_next(request)

    return RedirectResponse(f"/login?next={path}", status_code=303)


def check_password(candidate: str) -> bool:
    expected = os.getenv("DASHBOARD_PASSWORD", "")
    if not expected:
        return True
    return hmac.compare_digest(candidate, expected)
