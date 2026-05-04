"""Standalone scheduler process.

Runs APScheduler in a single dedicated process — separate from the
gunicorn web workers — so the daily digest fires exactly once per day.

Entry point: `pulse-scheduler` (see pyproject.toml).
"""

import asyncio
import logging
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from pulse_api.mailer import send_daily_digests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

LONDON = ZoneInfo("Europe/London")


def _run_digest() -> None:
    """APScheduler job — wraps the async digest in a sync call."""
    logger.info("[SCHEDULER] Firing daily digest job")
    try:
        result = asyncio.run(send_daily_digests())
        logger.info("[SCHEDULER] Digest finished: %s", result)
    except Exception:
        logger.exception("[SCHEDULER] Digest job raised")


def run() -> None:
    """Start the blocking scheduler. Blocks forever."""
    scheduler = BlockingScheduler(timezone=LONDON)
    scheduler.add_job(
        _run_digest,
        trigger=CronTrigger(hour=7, minute=30, timezone=LONDON),
        id="daily_digest",
        name="Daily email digest (07:30 Europe/London)",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=60 * 60,
    )
    logger.info(
        "[SCHEDULER] Started — daily digest scheduled for 07:30 Europe/London"
    )
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("[SCHEDULER] Shutting down")


if __name__ == "__main__":
    run()
