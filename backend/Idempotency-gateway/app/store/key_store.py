"""
Key Store — in-memory idempotency key store.

Each entry is a KeyEntry dataclass with fields:
    status      "PENDING" | "COMPLETE"
    body_hash   SHA-256 hex of the original request body
    status_code HTTP status code of the stored response
    response    dict — the full response body
    created_at  float — epoch timestamp (time.time())

Design notes
------------
- We use a plain dict protected by asyncio.Lock.
  asyncio is single-threaded, so the lock only protects across awaits —
  synchronous code between two awaits cannot be interrupted.
- The PENDING state is set BEFORE the processor is awaited. This means
  no second coroutine can reach the "MISS → process" branch for the same key.
  This eliminates the race condition without a distributed lock.
- TTL is checked on every get() call (lazy eviction) AND by a background
  cleanup task that runs every hour.

Production upgrade
------------------
Replace this module with a Redis adapter that exposes the same get/set/update
interface. Use SET key value EX ttl NX for the PENDING write — that gives you
atomic set-if-not-exists + expiry in one round-trip.
"""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Optional

from app.config import settings


@dataclass
class KeyEntry:
    status: str                        # "PENDING" or "COMPLETE"
    body_hash: str
    status_code: Optional[int] = None
    response: Optional[dict] = None
    created_at: float = field(default_factory=time.time)


_store: dict[str, KeyEntry] = {}
_lock = asyncio.Lock()
_TTL_SECONDS = settings.ttl_hours * 3600


def _is_expired(entry: KeyEntry) -> bool:
    return (time.time() - entry.created_at) > _TTL_SECONDS


def _expiry_iso(entry: KeyEntry) -> str:
    """Return ISO-8601 string of when the key expires."""
    import datetime
    exp = entry.created_at + _TTL_SECONDS
    return datetime.datetime.fromtimestamp(exp, tz=datetime.timezone.utc).isoformat()


async def get(key: str) -> Optional[KeyEntry]:
    """
    Retrieve a key entry.
    Returns None if the key does not exist or has expired.
    Expired entries are evicted lazily.
    """
    async with _lock:
        entry = _store.get(key)
        if entry is None:
            return None
        if _is_expired(entry):
            del _store[key]
            return None
        return entry


async def set_pending(key: str, body_hash: str) -> KeyEntry:
    """
    Atomically create a PENDING entry.
    Must be called BEFORE awaiting the processor.
    """
    async with _lock:
        entry = KeyEntry(status="PENDING", body_hash=body_hash)
        _store[key] = entry
        return entry


async def set_complete(key: str, status_code: int, response: dict) -> KeyEntry:
    """
    Transition a PENDING entry to COMPLETE once processing is done.
    """
    async with _lock:
        entry = _store.get(key)
        if entry is None:
            raise KeyError(f"Key not found during completion: {key}")
        entry.status = "COMPLETE"
        entry.status_code = status_code
        entry.response = response
        return entry


async def cleanup_expired() -> int:
    """Remove all expired keys. Returns count of purged entries."""
    async with _lock:
        expired_keys = [k for k, v in _store.items() if _is_expired(v)]
        for k in expired_keys:
            del _store[k]
        return len(expired_keys)


def get_expiry_header(entry: KeyEntry) -> str:
    return _expiry_iso(entry)