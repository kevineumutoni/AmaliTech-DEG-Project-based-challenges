"""
Alerter Service

Fires when a monitor's deadline is reached with no heartbeat.

Current implementation: logs a JSON-formatted critical alert to stdout.

Production upgrade path:
  - Send HTTP POST to a webhook URL stored on the monitor
  - Send email via SendGrid / Mailgun using alert_email
  - Push to PagerDuty, Slack, or SMS gateway
  - Write to a persistent alert log / database

The interface (fire_alert) stays identical regardless of backend.
"""

import logging
from datetime import datetime, timezone

from app.store.monitor_store import MonitorEntry, update

logger = logging.getLogger(__name__)


async def fire_alert(entry: MonitorEntry) -> None:
    """
    Mark the monitor as DOWN and emit a critical alert.
    Called by the scheduler when a deadline is missed.
    """
    entry.status = "DOWN"
    await update(entry)

    alert_payload = {
        "ALERT": f"Device {entry.id} is down!",
        "device_id": entry.id,
        "alert_email": entry.alert_email,
        "time": datetime.now(timezone.utc).isoformat(),
        "last_heartbeat": entry.last_heartbeat.isoformat() if entry.last_heartbeat else None,
    }

    # Structured critical log — in production replace/augment with real notifications
    logger.critical("DEVICE_DOWN | %s", alert_payload)

    # Also print plainly so it's unmissable in the terminal
    print(f"\n{'='*60}")
    print(f"ALERT: Device {entry.id!r} is down!")
    print(f"  alert_email : {entry.alert_email}")
    print(f"  time        : {alert_payload['time']}")
    print(f"  last_ping   : {alert_payload['last_heartbeat'] or 'never'}")
    print(f"{'='*60}\n")