"""Standalone scheduler process.

Runs APScheduler in a single dedicated process — separate from the
gunicorn web workers — so the daily digest fires exactly once per day.

Entry point: `pulse-scheduler` (see pyproject.toml).
"""

import asyncio
import logging
from datetime import datetime
from time import sleep
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from pulse_api.mailer import send_daily_digests
from pulse_api.sync import run_daily_sync

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


def _run_sync() -> None:
    """APScheduler job — nightly sync. Social sources (Instagram/Twitter)
    only run on Thursdays; other days are events-only."""
    today = datetime.now(LONDON)
    is_thursday = today.weekday() == 3  # Mon=0, Thu=3
    logger.info(
        "[SCHEDULER] Firing nightly sync (social=%s)", is_thursday,
    )
    try:
        result = asyncio.run(run_daily_sync(include_social=is_thursday))
        logger.info("[SCHEDULER] Sync finished: %s", result)
    except Exception:
        logger.exception("[SCHEDULER] Sync job raised")


def run() -> None:
    """Start the blocking scheduler. Blocks forever."""
    scheduler = BlockingScheduler(timezone=LONDON)
    scheduler.add_job(
        _run_sync,
        trigger=CronTrigger(hour=2, minute=0, timezone=LONDON),
        id="nightly_sync",
        name="Nightly sync (02:00 Europe/London, social on Thursdays)",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=60 * 60,
    )
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
        "[SCHEDULER] Started — nightly sync 02:00 (social Thu only), "
        "digest 07:30 Europe/London"
    )
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("[SCHEDULER] Shutting down")


if __name__ == "__main__":
    run()
