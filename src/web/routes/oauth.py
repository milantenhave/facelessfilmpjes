"""OAuth routes for connecting a channel to YouTube."""
from __future__ import annotations

import secrets

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ...uploader import oauth_youtube

router = APIRouter()


@router.get("/youtube/start")
async def start_youtube_oauth(request: Request, channel_id: int):
    state_token = secrets.token_urlsafe(24)
    request.session["yt_oauth_state"] = state_token
    request.session["yt_oauth_channel"] = channel_id
    try:
        url = oauth_youtube.build_auth_url(state_token)
    except RuntimeError as exc:
        raise HTTPException(400, str(exc))
    return RedirectResponse(url, status_code=303)


@router.get("/youtube/callback", response_class=HTMLResponse)
async def youtube_callback(request: Request, code: str | None = None,
                           state: str | None = None, error: str | None = None):
    if error:
        return HTMLResponse(f"<pre>YouTube OAuth error: {error}</pre>",
                            status_code=400)
    if not code:
        raise HTTPException(400, "missing code")

    expected_state = request.session.get("yt_oauth_state")
    channel_id = request.session.get("yt_oauth_channel")
    if not expected_state or expected_state != state or not channel_id:
        raise HTTPException(400, "invalid OAuth state")

    creds = oauth_youtube.exchange_code(code)
    token = oauth_youtube.save_token(int(channel_id), creds)

    request.session.pop("yt_oauth_state", None)
    request.session.pop("yt_oauth_channel", None)

    return HTMLResponse(
        f"""<!doctype html>
        <html><body style="font-family: system-ui; padding: 2rem;">
        <h2>YouTube connected ✓</h2>
        <p>Channel: <b>{token.account_name}</b></p>
        <p><a href="/channels">Back to channels</a></p>
        </body></html>"""
    )


@router.post("/youtube/disconnect/{channel_id}")
async def disconnect_youtube(channel_id: int):
    from ...db import OAuthToken, Platform, session_scope
    with session_scope() as s:
        tok = s.query(OAuthToken).filter_by(
            channel_id=channel_id, platform=Platform.youtube,
        ).one_or_none()
        if tok:
            s.delete(tok)
    return RedirectResponse("/channels", status_code=303)
