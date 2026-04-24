"""In-process pub/sub for live job status.

Workers publish events; the FastAPI SSE endpoint subscribes. Both the worker
thread and the uvicorn event loop live in the same process, so a simple
`asyncio.Queue` per subscriber is enough — no Redis required for solo use.
"""
from __future__ import annotations

import asyncio
import json
import threading
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any


@dataclass
class Event:
    kind: str                    # job.update | job.log | system
    job_id: int | None = None
    channel_id: int | None = None
    payload: dict[str, Any] | None = None
    ts: str = ""

    def to_json(self) -> str:
        d = asdict(self)
        if not d["ts"]:
            d["ts"] = datetime.now(timezone.utc).isoformat()
        return json.dumps(d, ensure_ascii=False)


class EventBus:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[Event]] = set()
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self) -> asyncio.Queue[Event]:
        q: asyncio.Queue[Event] = asyncio.Queue(maxsize=500)
        with self._lock:
            self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[Event]) -> None:
        with self._lock:
            self._subscribers.discard(q)

    def publish(self, event: Event) -> None:
        """Thread-safe fan-out. Safe to call from worker threads."""
        loop = self._loop
        with self._lock:
            subs = list(self._subscribers)
        for q in subs:
            if loop is not None and loop.is_running():
                loop.call_soon_threadsafe(self._try_put, q, event)
            else:
                try:
                    q.put_nowait(event)
                except asyncio.QueueFull:
                    pass

    @staticmethod
    def _try_put(q: asyncio.Queue[Event], event: Event) -> None:
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            # Drop oldest if backed up; a live status UI prefers fresh over full.
            try:
                q.get_nowait()
                q.put_nowait(event)
            except Exception:
                pass


bus = EventBus()


def publish_job_update(job_id: int, channel_id: int | None,
                       status: str, progress: float,
                       detail: str = "", **extra: Any) -> None:
    bus.publish(Event(
        kind="job.update",
        job_id=job_id,
        channel_id=channel_id,
        payload={"status": status, "progress": round(progress, 2),
                 "detail": detail, **extra},
    ))


def publish_log(job_id: int | None, message: str, level: str = "info") -> None:
    bus.publish(Event(
        kind="job.log",
        job_id=job_id,
        payload={"message": message, "level": level},
    ))
