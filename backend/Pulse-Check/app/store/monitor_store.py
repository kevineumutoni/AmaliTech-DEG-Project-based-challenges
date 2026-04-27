"""
Monitor Store — in-memory state for all registered monitors.

Each MonitorEntry holds:
    id              str
    timeout         int          original timeout in seconds
    alert_email     str
    status          str          "ACTIVE" | "PAUSED" | "DOWN"
    deadline        datetime     UTC — when the timer expires
    last_heartbeat  datetime|None
    created_at      datetime

Design note
-----------
A plain dict protected by asyncio.Lock.  Because asyncio is single-threaded,
the lock only needs to guard across awaits — all synchronous reads/writes
between two awaits are implicitly atomic.

Production upgrade: replace with Redis HASH + sorted set on deadline.
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict

_store: Dict[str, "MonitorEntry"] = {}
_lock = asyncio.Lock()


@dataclass
class MonitorEntry:
    id: str
    timeout: int
    alert_email: str
    status: str = "ACTIVE"          # ACTIVE | PAUSED | DOWN
    deadline: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_heartbeat: Optional[datetime] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def reset_deadline(self) -> None:
        """Push deadline forward by timeout seconds from now."""
        self.deadline = datetime.now(timezone.utc) + timedelta(seconds=self.timeout)

    def is_expired(self) -> bool:
        return (
            self.status == "ACTIVE"
            and datetime.now(timezone.utc) >= self.deadline
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "timeout": self.timeout,
            "alert_email": self.alert_email,
            "status": self.status,
            "deadline": self.deadline,
            "last_heartbeat": self.last_heartbeat,
            "created_at": self.created_at,
        }


async def create(entry: MonitorEntry) -> MonitorEntry:
    async with _lock:
        _store[entry.id] = entry
        return entry


async def get(monitor_id: str) -> Optional[MonitorEntry]:
    async with _lock:
        return _store.get(monitor_id)


async def get_all() -> list[MonitorEntry]:
    async with _lock:
        return list(_store.values())


async def update(entry: MonitorEntry) -> MonitorEntry:
    async with _lock:
        _store[entry.id] = entry
        return entry


async def delete(monitor_id: str) -> bool:
    async with _lock:
        if monitor_id in _store:
            del _store[monitor_id]
            return True
        return False


async def get_expired() -> list[MonitorEntry]:
    """Return all ACTIVE monitors whose deadline has passed."""
    async with _lock:
        return [e for e in _store.values() if e.is_expired()]