"""APScheduler-based job scheduler for K-Quant pipeline."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Callable, Coroutine

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)


class PipelineScheduler:
    """Manages scheduled pipeline jobs using APScheduler.

    Jobs run in KST timezone. Default schedule:
    - EOD scan: 16:00 KST (after market close)
    - Macro update: 08:45 KST (before market open)
    """

    def __init__(self) -> None:
        self.scheduler = AsyncIOScheduler(timezone="Asia/Seoul")

    def add_eod_scan(
        self, func: Callable[..., Coroutine], hour: int = 16, minute: int = 0
    ) -> None:
        """Add EOD scan job (default 16:00 KST)."""
        self.scheduler.add_job(
            func,
            CronTrigger(hour=hour, minute=minute, timezone="Asia/Seoul"),
            id="eod_scan",
            name="EOD Market Scan",
            replace_existing=True,
        )
        logger.info("EOD scan scheduled at %02d:%02d KST", hour, minute)

    def add_macro_update(
        self, func: Callable[..., Coroutine], hour: int = 8, minute: int = 45
    ) -> None:
        """Add macro update job (default 08:45 KST)."""
        self.scheduler.add_job(
            func,
            CronTrigger(hour=hour, minute=minute, timezone="Asia/Seoul"),
            id="macro_update",
            name="Macro Data Update",
            replace_existing=True,
        )
        logger.info("Macro update scheduled at %02d:%02d KST", hour, minute)

    def start(self) -> None:
        """Start the scheduler."""
        self.scheduler.start()
        logger.info("Scheduler started with %d jobs", len(self.scheduler.get_jobs()))

    def shutdown(self) -> None:
        """Shutdown the scheduler."""
        self.scheduler.shutdown()
        logger.info("Scheduler stopped")

    def get_jobs_info(self) -> list[dict]:
        """Get info about all scheduled jobs."""
        jobs = []
        for job in self.scheduler.get_jobs():
            jobs.append(
                {
                    "id": job.id,
                    "name": job.name,
                    "next_run": str(job.next_run_time) if job.next_run_time else "N/A",
                }
            )
        return jobs
