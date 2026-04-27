"""
Test suite — covers all five acceptance criteria from the challenge spec.

Run with:  pytest tests/ -v
"""

import asyncio
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.store import key_store


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture(autouse=True)
async def clear_store():
    """Wipe the key store before each test for isolation."""
    key_store._store.clear()
    yield
    key_store._store.clear()


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac


# ── Helpers ───────────────────────────────────────────────────────────────────

VALID_BODY = {"amount": 100, "currency": "GHS"}
HEADERS = lambda key: {"Idempotency-Key": key, "Content-Type": "application/json"}


# ── US-1: Happy Path ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_first_request_returns_201(client):
    resp = await client.post(
        "/process-payment", json=VALID_BODY, headers=HEADERS("test-key-001")
    )
    assert resp.status_code == 201
    body = resp.json()
    assert "transaction_id" in body
    assert body["status"] == "Charged 100.0 GHS"
    assert "processed_at" in body


@pytest.mark.asyncio
async def test_first_request_no_cache_hit_header(client):
    resp = await client.post(
        "/process-payment", json=VALID_BODY, headers=HEADERS("test-key-002")
    )
    assert "x-cache-hit" not in resp.headers


@pytest.mark.asyncio
async def test_expiry_header_present(client):
    resp = await client.post(
        "/process-payment", json=VALID_BODY, headers=HEADERS("test-key-003")
    )
    assert "x-idempotency-key-expires" in resp.headers


# ── US-2: Duplicate Request ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_duplicate_returns_same_response(client):
    key = "test-key-dup-001"
    first = await client.post("/process-payment", json=VALID_BODY, headers=HEADERS(key))
    second = await client.post("/process-payment", json=VALID_BODY, headers=HEADERS(key))

    assert second.status_code == 201
    assert second.json() == first.json()                 # identical body
    assert second.headers.get("x-cache-hit") == "true"  # marked as replay


@pytest.mark.asyncio
async def test_duplicate_transaction_id_unchanged(client):
    key = "test-key-dup-002"
    first = await client.post("/process-payment", json=VALID_BODY, headers=HEADERS(key))
    second = await client.post("/process-payment", json=VALID_BODY, headers=HEADERS(key))

    assert first.json()["transaction_id"] == second.json()["transaction_id"]


# ── US-3: Fraud / Conflict Check ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_same_key_different_body_returns_409(client):
    key = "test-key-conflict-001"
    await client.post("/process-payment", json=VALID_BODY, headers=HEADERS(key))

    different_body = {"amount": 500, "currency": "GHS"}
    resp = await client.post("/process-payment", json=different_body, headers=HEADERS(key))

    assert resp.status_code == 409
    assert "different request body" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_same_key_different_currency_returns_409(client):
    key = "test-key-conflict-002"
    await client.post("/process-payment", json=VALID_BODY, headers=HEADERS(key))

    resp = await client.post(
        "/process-payment", json={"amount": 100, "currency": "USD"}, headers=HEADERS(key)
    )
    assert resp.status_code == 409


# ── US-4: Missing Header ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_missing_idempotency_key_returns_400(client):
    resp = await client.post(
        "/process-payment",
        json=VALID_BODY,
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 400
    assert "Idempotency-Key" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_key_too_long_returns_400(client):
    resp = await client.post(
        "/process-payment",
        json=VALID_BODY,
        headers=HEADERS("x" * 256),
    )
    assert resp.status_code == 400


# ── US-5: Input validation ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_negative_amount_rejected(client):
    resp = await client.post(
        "/process-payment",
        json={"amount": -50, "currency": "GHS"},
        headers=HEADERS("test-key-val-001"),
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_missing_currency_rejected(client):
    resp = await client.post(
        "/process-payment",
        json={"amount": 100},
        headers=HEADERS("test-key-val-002"),
    )
    assert resp.status_code == 422


# ── Bonus: Race condition (in-flight / PENDING state) ────────────────────────

@pytest.mark.asyncio
async def test_concurrent_duplicate_requests_only_process_once(client):
    """
    Fire two identical requests concurrently.
    Only one should trigger the processor — both should receive the same result.
    """
    key = "test-key-race-001"
    results = await asyncio.gather(
        client.post("/process-payment", json=VALID_BODY, headers=HEADERS(key)),
        client.post("/process-payment", json=VALID_BODY, headers=HEADERS(key)),
    )
    codes = [r.status_code for r in results]
    bodies = [r.json() for r in results]

    assert all(c == 201 for c in codes), f"Expected all 201, got {codes}"
    # Both responses must have the SAME transaction_id — only processed once
    assert bodies[0]["transaction_id"] == bodies[1]["transaction_id"], (
        "Race condition detected: two different transaction_ids means double processing"
    )


# ── Developer's Choice: TTL / Key Expiry ─────────────────────────────────────

@pytest.mark.asyncio
async def test_expired_key_treated_as_new(client):
    """
    Manually expire a key and verify the next request is treated as new.
    """
    key = "test-key-ttl-001"
    first = await client.post("/process-payment", json=VALID_BODY, headers=HEADERS(key))
    first_tid = first.json()["transaction_id"]

    # Fast-forward: artificially age the entry past TTL
    entry = key_store._store.get(key)
    entry.created_at -= (key_store._TTL_SECONDS + 10)

    second = await client.post("/process-payment", json=VALID_BODY, headers=HEADERS(key))
    second_tid = second.json()["transaction_id"]

    assert second.status_code == 201
    assert second_tid != first_tid, "Expired key should produce a new transaction"
    assert second.headers.get("x-cache-hit") is None


# ── Health check ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_health_check(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"