"""
Scheduler — the heartbeat watchdog.

Runs as a background asyncio task from application startup.
Every SCHEDULER_TICK_SECONDS it:
  1. Fetches all ACTIVE monitors whose deadline has passed
  2. Calls fire_alert() for each expired monitor

This is the "Dead Man's Switch" trigger mechanism.

Single-node note
----------------
asyncio.sleep() yields control so other coroutines (HTTP handlers) run freely.
The scheduler never blocks the event loop for more than O(N) dict reads,
which is negligible at any realistic device count.

Production upgrade
------------------
Replace the polling loop with a Redis sorted-set:
  - ZADD monitors:deadlines <epoch_score> <device_id>
  - ZRANGEBYSCORE monitors:deadlines 0 <now_epoch>  → expired devices
  - ZREM after processing
This is O(log N) per poll instead of O(N) scan.
"""

import asyncio
import logging

from app.config import settings
from app.store.monitor_store import get_expired
from app.services.alerter import fire_alert

logger = logging.getLogger(__name__)


async def watchdog_loop() -> None:
    """Infinite loop: check for expired monitors, fire alerts, sleep, repeat."""
    logger.info("Watchdog scheduler started (tick=%.1fs)", settings.scheduler_tick_seconds)
    while True:
        try:
            expired = await get_expired()
            for entry in expired:
                logger.info("Watchdog: firing alert for %s", entry.id)
                await fire_alert(entry)
        except Exception as exc:
            # Never let the scheduler crash — log and continue
            logger.error("Scheduler error: %s", exc, exc_info=True)

        await asyncio.sleep(settings.scheduler_tick_seconds)