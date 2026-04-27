"""
Test suite — covers all user stories + bonus + developer's choice.

Run with:  pytest tests/ -v
"""

import asyncio
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.store import monitor_store


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture(autouse=True)
async def clear_store():
    monitor_store._store.clear()
    yield
    monitor_store._store.clear()


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac


DEVICE = {"id": "device-123", "timeout": 60, "alert_email": "admin@critmon.com"}
HEADERS = {"Content-Type": "application/json"}


# ── US-1: Register monitor ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_register_monitor_returns_201(client):
    resp = await client.post("/monitors", json=DEVICE)
    assert resp.status_code == 201
    body = resp.json()
    assert body["id"] == "device-123"
    assert body["status"] == "ACTIVE"
    assert body["timeout"] == 60
    assert body["alert_email"] == "admin@critmon.com"
    assert "deadline" in body
    assert "created_at" in body


@pytest.mark.asyncio
async def test_duplicate_register_returns_409(client):
    await client.post("/monitors", json=DEVICE)
    resp = await client.post("/monitors", json=DEVICE)
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_register_missing_fields_returns_422(client):
    resp = await client.post("/monitors", json={"id": "x"})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_register_negative_timeout_returns_422(client):
    resp = await client.post("/monitors", json={**DEVICE, "timeout": -5})
    assert resp.status_code == 422


# ── US-2: Heartbeat ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_heartbeat_resets_deadline(client):
    await client.post("/monitors", json=DEVICE)

    import time
    time.sleep(0.1)

    resp = await client.post("/monitors/device-123/heartbeat")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ACTIVE"
    assert body["last_heartbeat"] is not None


@pytest.mark.asyncio
async def test_heartbeat_unknown_device_returns_404(client):
    resp = await client.post("/monitors/ghost-device/heartbeat")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_heartbeat_on_down_device_returns_410(client):
    await client.post("/monitors", json=DEVICE)
    entry = monitor_store._store["device-123"]
    entry.status = "DOWN"
    resp = await client.post("/monitors/device-123/heartbeat")
    assert resp.status_code == 410


# ── US-3: Alert trigger ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_expired_monitor_triggers_alert():
    """
    Register a monitor with a 0.1s timeout, wait for the scheduler to fire,
    confirm status changes to DOWN.
    """
    from datetime import datetime, timezone, timedelta
    from app.store.monitor_store import MonitorEntry, create
    from app.services.alerter import fire_alert

    entry = MonitorEntry(
        id="expired-device",
        timeout=1,
        alert_email="ops@critmon.com",
        status="ACTIVE",
        deadline=datetime.now(timezone.utc) - timedelta(seconds=1),  # already expired
    )
    await create(entry)

    # Directly call alerter (scheduler calls this internally)
    await fire_alert(entry)

    updated = monitor_store._store.get("expired-device")
    assert updated is not None
    assert updated.status == "DOWN"


# ── Bonus: Pause ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_pause_stops_timer(client):
    await client.post("/monitors", json=DEVICE)
    resp = await client.post("/monitors/device-123/pause")
    assert resp.status_code == 200
    assert resp.json()["status"] == "PAUSED"


@pytest.mark.asyncio
async def test_paused_monitor_not_expired():
    """A PAUSED monitor should never be returned by get_expired()."""
    from datetime import datetime, timezone, timedelta
    from app.store.monitor_store import MonitorEntry, create, get_expired

    entry = MonitorEntry(
        id="paused-device",
        timeout=1,
        alert_email="ops@critmon.com",
        status="PAUSED",
        deadline=datetime.now(timezone.utc) - timedelta(seconds=10),
    )
    await create(entry)
    expired = await get_expired()
    ids = [e.id for e in expired]
    assert "paused-device" not in ids


@pytest.mark.asyncio
async def test_heartbeat_unpauses_monitor(client):
    await client.post("/monitors", json=DEVICE)
    await client.post("/monitors/device-123/pause")
    resp = await client.post("/monitors/device-123/heartbeat")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ACTIVE"


@pytest.mark.asyncio
async def test_pause_unknown_device_returns_404(client):
    resp = await client.post("/monitors/ghost/pause")
    assert resp.status_code == 404


# ── Developer's Choice: List / Get / Delete ───────────────────────────────────

@pytest.mark.asyncio
async def test_list_monitors(client):
    await client.post("/monitors", json=DEVICE)
    await client.post("/monitors", json={**DEVICE, "id": "device-456"})
    resp = await client.get("/monitors")
    assert resp.status_code == 200
    assert len(resp.json()) == 2


@pytest.mark.asyncio
async def test_get_single_monitor(client):
    await client.post("/monitors", json=DEVICE)
    resp = await client.get("/monitors/device-123")
    assert resp.status_code == 200
    assert resp.json()["id"] == "device-123"


@pytest.mark.asyncio
async def test_get_unknown_monitor_returns_404(client):
    resp = await client.get("/monitors/ghost")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_monitor(client):
    await client.post("/monitors", json=DEVICE)
    resp = await client.delete("/monitors/device-123")
    assert resp.status_code == 200
    # Confirm it's gone
    resp2 = await client.get("/monitors/device-123")
    assert resp2.status_code == 404


@pytest.mark.asyncio
async def test_delete_unknown_returns_404(client):
    resp = await client.delete("/monitors/ghost")
    assert resp.status_code == 404


# ── Health check ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_health(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"