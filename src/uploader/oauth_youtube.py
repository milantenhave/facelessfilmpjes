"""Google OAuth2 flow for YouTube upload credentials, per channel."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Optional

from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

from ..db import OAuthToken, Platform, session_scope
from ..utils.logger import get_logger

log = get_logger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/userinfo.email",
    "openid",
]


def _client_config() -> dict:
    cid = os.getenv("GOOGLE_CLIENT_ID", "")
    secret = os.getenv("GOOGLE_CLIENT_SECRET", "")
    if not cid or not secret:
        raise RuntimeError(
            "GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET missing. See .env.example."
        )
    return {
        "web": {
            "client_id": cid,
            "client_secret": secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [_redirect_uri()],
        }
    }


def _redirect_uri() -> str:
    base = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")
    return f"{base}/oauth/youtube/callback"


def build_auth_url(state: str) -> str:
    flow = Flow.from_client_config(_client_config(), scopes=SCOPES,
                                    redirect_uri=_redirect_uri())
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",          # force refresh_token
        state=state,
    )
    return auth_url


def exchange_code(code: str) -> Credentials:
    flow = Flow.from_client_config(_client_config(), scopes=SCOPES,
                                    redirect_uri=_redirect_uri())
    flow.fetch_token(code=code)
    return flow.credentials


def save_token(channel_id: int, creds: Credentials) -> OAuthToken:
    yt = build("youtube", "v3", credentials=creds, cache_discovery=False)
    me = yt.channels().list(part="id,snippet", mine=True).execute()
    items = me.get("items") or []
    account_id = items[0]["id"] if items else ""
    account_name = items[0]["snippet"]["title"] if items else ""

    with session_scope() as s:
        existing = s.query(OAuthToken).filter_by(
            channel_id=channel_id, platform=Platform.youtube,
        ).one_or_none()
        expires_at = creds.expiry.replace(tzinfo=timezone.utc) \
            if creds.expiry else None
        data = {
            "access_token": creds.token,
            "refresh_token": creds.refresh_token or (existing.refresh_token
                                                     if existing else ""),
            "token_type": "Bearer",
            "expires_at": expires_at,
            "scopes": " ".join(creds.scopes or SCOPES),
            "account_id": account_id,
            "account_name": account_name,
        }
        if existing:
            for k, v in data.items():
                setattr(existing, k, v)
            tok = existing
        else:
            tok = OAuthToken(channel_id=channel_id,
                             platform=Platform.youtube, **data)
            s.add(tok)
        s.flush()
        s.refresh(tok)
        return tok


def load_credentials(channel_id: int) -> Optional[Credentials]:
    with session_scope() as s:
        tok = s.query(OAuthToken).filter_by(
            channel_id=channel_id, platform=Platform.youtube,
        ).one_or_none()
        if not tok:
            return None
        cfg = _client_config()["web"]
        creds = Credentials(
            token=tok.access_token,
            refresh_token=tok.refresh_token or None,
            token_uri=cfg["token_uri"],
            client_id=cfg["client_id"],
            client_secret=cfg["client_secret"],
            scopes=(tok.scopes or "").split() or SCOPES,
        )
        if tok.expires_at:
            creds.expiry = tok.expires_at.replace(tzinfo=None)

    if not creds.valid:
        try:
            creds.refresh(GoogleRequest())
        except Exception as exc:  # noqa: BLE001
            log.warning("YouTube token refresh failed for channel %d: %s",
                        channel_id, exc)
            return None
        # Persist refreshed access token
        with session_scope() as s:
            tok = s.query(OAuthToken).filter_by(
                channel_id=channel_id, platform=Platform.youtube,
            ).one_or_none()
            if tok:
                tok.access_token = creds.token
                if creds.expiry:
                    tok.expires_at = creds.expiry.replace(tzinfo=timezone.utc)
    return creds
