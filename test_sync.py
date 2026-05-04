"""Run the daily sync for all tracked artists."""
import asyncio
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s  %(message)s",
    datefmt="%H:%M:%S",
)

from pulse_api.sync import run_daily_sync

asyncio.run(run_daily_sync())
