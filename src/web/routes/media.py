"""Serve temporary files so Creatomate (and similar cloud renderers) can
fetch voice-over audio and fallback clips by URL.

Tokens are opaque, short-lived references to paths registered by the worker.
"""
from __future__ import annotations

import mimetypes
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from ...worker.runner import resolve_temp_file

router = APIRouter()


@router.get("/tmp/{token}")
async def get_tmp(token: str):
    path = resolve_temp_file(token)
    if not path or not Path(path).exists():
        raise HTTPException(404, "not found")
    mime, _ = mimetypes.guess_type(str(path))
    return FileResponse(str(path), media_type=mime or "application/octet-stream")
