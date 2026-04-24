"""Simple in-process scheduler.

Prefer cron on a VPS (see README). This module is provided so the pipeline can
run unattended on a workstation or in a long-lived container without a crond.
"""
from __future__ import annotations

import time
from typing import Callable

import schedule

from ..utils.logger import get_logger

log = get_logger(__name__)


class Scheduler:
    def __init__(self, cfg: dict, runner: Callable[[], None]) -> None:
        self.cfg = cfg
        self.runner = runner
        self.frequency = cfg.get("posting_frequency", "manual")

    def run_forever(self) -> None:
        if self.frequency == "manual":
            log.info("posting_frequency=manual; running once and exiting.")
            self.runner()
            return

        if self.frequency == "hourly":
            schedule.every().hour.at(":00").do(self._tick)
        elif self.frequency == "daily":
            schedule.every().day.at("09:00").do(self._tick)
        else:
            # Treat custom strings like "30m" or "2h".
            unit = self.frequency[-1]
            amount = int(self.frequency[:-1])
            if unit == "m":
                schedule.every(amount).minutes.do(self._tick)
            elif unit == "h":
                schedule.every(amount).hours.do(self._tick)
            else:
                raise ValueError(f"unknown posting_frequency={self.frequency!r}")

        log.info("scheduler started (frequency=%s)", self.frequency)
        self._tick()  # run immediately on startup
        while True:
            schedule.run_pending()
            time.sleep(10)

    def _tick(self) -> None:
        try:
            self.runner()
        except Exception:   # noqa: BLE001
            log.exception("scheduled run failed")
