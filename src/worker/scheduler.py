"""APScheduler wrapper that ticks active channel schedules."""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from ..db import Channel, Job, JobStatus, Schedule, session_scope
from ..events import publish_log
from ..utils.logger import get_logger
from .runner import JobRunner

log = get_logger(__name__)


class WorkerScheduler:
    """One BackgroundScheduler + one long-lived worker thread for job execution.

    Scheduler fires insert `Job` rows in `pending`, the worker thread pulls
    them FIFO and runs them one at a time (the VPS has 1 core — serial is fine).
    """

    def __init__(self) -> None:
        self.scheduler = BackgroundScheduler(timezone="UTC")
        self.runner = JobRunner()
        self._stop = threading.Event()
        self._worker: Optional[threading.Thread] = None
        self._wake = threading.Event()

    # -- lifecycle -----------------------------------------------------
    def start(self) -> None:
        self.reload_schedules()
        self.scheduler.start()
        self._worker = threading.Thread(target=self._work_loop,
                                        name="faceless-worker",
                                        daemon=True)
        self._worker.start()
        log.info("scheduler + worker started")

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        try:
            self.scheduler.shutdown(wait=False)
        except Exception:   # noqa: BLE001
            pass
        log.info("scheduler stopped")

    # -- schedules -----------------------------------------------------
    def reload_schedules(self) -> None:
        """Sync APScheduler jobs with the Schedule rows in the DB."""
        with session_scope() as s:
            rows = s.query(Schedule).filter_by(active=True).all()
            plan = [(r.id, r.channel_id, r.cron, r.videos_per_slot) for r in rows]

        # Drop existing schedule-triggered jobs and re-add.
        for job in self.scheduler.get_jobs():
            if job.id.startswith("sched-"):
                self.scheduler.remove_job(job.id)

        for sched_id, channel_id, cron, slots in plan:
            try:
                trigger = CronTrigger.from_crontab(cron, timezone="UTC")
            except Exception as exc:  # noqa: BLE001
                log.warning("schedule %d has invalid cron %r: %s",
                            sched_id, cron, exc)
                continue
            self.scheduler.add_job(
                self._enqueue_for_schedule,
                trigger=trigger,
                id=f"sched-{sched_id}",
                args=[sched_id, channel_id, slots],
                replace_existing=True,
            )
        log.info("reloaded %d schedules", len(plan))

    def _enqueue_for_schedule(self, sched_id: int, channel_id: int,
                              slots: int) -> None:
        with session_scope() as s:
            channel = s.get(Channel, channel_id)
            if not channel or not channel.active:
                return
            niche_id = channel.niche_id
            for _ in range(max(1, slots)):
                job = Job(channel_id=channel.id, niche_id=niche_id,
                          status=JobStatus.pending)
                s.add(job)
            sched = s.get(Schedule, sched_id)
            if sched:
                sched.last_run_at = datetime.now(timezone.utc)
        publish_log(None, f"scheduled tick for channel {channel_id} "
                    f"(enqueued {slots})")
        self._wake.set()

    # -- manual trigger ------------------------------------------------
    def enqueue_now(self, channel_id: int,
                    niche_id: Optional[int] = None) -> int:
        with session_scope() as s:
            channel = s.get(Channel, channel_id)
            if not channel:
                raise RuntimeError(f"channel {channel_id} not found")
            job = Job(channel_id=channel_id,
                      niche_id=niche_id or channel.niche_id,
                      status=JobStatus.pending)
            s.add(job); s.flush()
            job_id = job.id
        self._wake.set()
        return job_id

    # -- worker --------------------------------------------------------
    def _work_loop(self) -> None:
        while not self._stop.is_set():
            job_id = self._next_pending_job()
            if job_id is None:
                self._wake.wait(timeout=10)
                self._wake.clear()
                continue
            try:
                self.runner.run(job_id)
            except Exception:   # noqa: BLE001
                log.exception("runner crashed on job %d", job_id)

    @staticmethod
    def _next_pending_job() -> Optional[int]:
        with session_scope() as s:
            job = (s.query(Job)
                    .filter_by(status=JobStatus.pending)
                    .order_by(Job.created_at.asc())
                    .first())
            if not job:
                return None
            job.status = JobStatus.scripting
            s.flush()
            return job.id
