"""
Monitor Router — all /monitors endpoints.

Endpoints
---------
POST   /monitors              Register a new monitor
GET    /monitors              List all monitors            (Developer's Choice)
GET    /monitors/{id}         Get a single monitor         (Developer's Choice)
POST   /monitors/{id}/heartbeat  Reset countdown
POST   /monitors/{id}/pause   Pause the countdown          (Bonus)
DELETE /monitors/{id}         Delete a monitor             (Developer's Choice)
"""

import logging
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, HTTPException

from app.models.monitor import CreateMonitorRequest, MonitorResponse, MessageResponse
from app.store import monitor_store
from app.store.monitor_store import MonitorEntry

router = APIRouter(prefix="/monitors", tags=["Monitors"])
logger = logging.getLogger(__name__)


def _to_response(entry: MonitorEntry) -> MonitorResponse:
    return MonitorResponse(**entry.to_dict())


# ── US-1: Register a monitor ──────────────────────────────────────────────────

@router.post("", status_code=201, response_model=MonitorResponse)
async def register_monitor(body: CreateMonitorRequest):
    """
    Register a new device monitor and start the countdown.

    - Creates a monitor entry with status=ACTIVE
    - Sets deadline = now + timeout seconds
    - If a monitor with the same ID already exists, returns 409
    """
    existing = await monitor_store.get(body.id)
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"Monitor '{body.id}' already exists. Delete it first or send a heartbeat.",
        )

    now = datetime.now(timezone.utc)
    entry = MonitorEntry(
        id=body.id,
        timeout=body.timeout,
        alert_email=body.alert_email,
        status="ACTIVE",
        deadline=now + timedelta(seconds=body.timeout),
        last_heartbeat=None,
        created_at=now,
    )
    await monitor_store.create(entry)
    logger.info("Monitor registered: %s (timeout=%ds)", body.id, body.timeout)
    return _to_response(entry)


# ── Developer's Choice: List + Get ───────────────────────────────────────────

@router.get("", response_model=list[MonitorResponse])
async def list_monitors():
    """Return all registered monitors sorted by created_at descending."""
    all_monitors = await monitor_store.get_all()
    return [_to_response(e) for e in sorted(all_monitors, key=lambda e: e.created_at, reverse=True)]


@router.get("/{monitor_id}", response_model=MonitorResponse)
async def get_monitor(monitor_id: str):
    """Return a single monitor by ID."""
    entry = await monitor_store.get(monitor_id)
    if not entry:
        raise HTTPException(status_code=404, detail=f"Monitor '{monitor_id}' not found.")
    return _to_response(entry)


# ── US-2: Heartbeat ───────────────────────────────────────────────────────────

@router.post("/{monitor_id}/heartbeat", response_model=MonitorResponse)
async def heartbeat(monitor_id: str):
    """
    Reset the countdown for a monitor.

    - If monitor does not exist → 404
    - If monitor is DOWN (alert already fired) → 410 Gone
    - If monitor is PAUSED → un-pauses it, restarts the timer, returns ACTIVE
    - If monitor is ACTIVE → resets deadline, returns ACTIVE
    """
    entry = await monitor_store.get(monitor_id)
    if not entry:
        raise HTTPException(status_code=404, detail=f"Monitor '{monitor_id}' not found.")

    if entry.status == "DOWN":
        raise HTTPException(
            status_code=410,
            detail=f"Monitor '{monitor_id}' is DOWN. Re-register to resume monitoring.",
        )

    # Heartbeat un-pauses a paused monitor automatically (Bonus requirement)
    entry.status = "ACTIVE"
    entry.last_heartbeat = datetime.now(timezone.utc)
    entry.reset_deadline()
    await monitor_store.update(entry)

    logger.info("Heartbeat received: %s (deadline reset)", monitor_id)
    return _to_response(entry)


# ── Bonus: Pause ──────────────────────────────────────────────────────────────

@router.post("/{monitor_id}/pause", response_model=MonitorResponse)
async def pause_monitor(monitor_id: str):
    """
    Pause the countdown for a monitor (maintenance mode).

    - Stops the timer — no alert will fire while PAUSED
    - Calling /heartbeat will automatically un-pause and restart the timer
    - If monitor is DOWN → 410 Gone (cannot pause a dead monitor)
    """
    entry = await monitor_store.get(monitor_id)
    if not entry:
        raise HTTPException(status_code=404, detail=f"Monitor '{monitor_id}' not found.")

    if entry.status == "DOWN":
        raise HTTPException(
            status_code=410,
            detail=f"Monitor '{monitor_id}' is already DOWN.",
        )

    if entry.status == "PAUSED":
        return _to_response(entry)  # idempotent — already paused

    entry.status = "PAUSED"
    await monitor_store.update(entry)
    logger.info("Monitor paused: %s", monitor_id)
    return _to_response(entry)


# ── Developer's Choice: Delete ────────────────────────────────────────────────

@router.delete("/{monitor_id}", response_model=MessageResponse)
async def delete_monitor(monitor_id: str):
    """
    Remove a monitor entirely.
    Useful for decommissioned devices or after a DOWN alert is resolved.
    """
    deleted = await monitor_store.delete(monitor_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Monitor '{monitor_id}' not found.")
    logger.info("Monitor deleted: %s", monitor_id)
    return {"message": f"Monitor '{monitor_id}' deleted."}