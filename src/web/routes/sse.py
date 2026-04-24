"""Server-Sent Events for live job status on the dashboard."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse

from ...events import bus

router = APIRouter()


@router.get("/events")
async def events(request: Request):
    queue = bus.subscribe()

    async def stream():
        try:
            # Send a small hello so the client knows the stream is live.
            yield {"event": "hello", "data": "{}"}
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=20)
                    yield {"event": event.kind, "data": event.to_json()}
                except asyncio.TimeoutError:
                    # keep-alive comment
                    yield {"event": "ping", "data": "{}"}
        finally:
            bus.unsubscribe(queue)

    return EventSourceResponse(stream())
