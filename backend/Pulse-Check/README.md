# Pulse-Check API — Watchdog Sentinel

A **Dead Man's Switch API** for CritMon Servers Inc. Remote devices (solar farms, weather stations) register a monitor with a countdown timer. If they fail to send a heartbeat before the timer expires, the system fires an alert automatically.

---

## Architecture Diagram

```
┌──────────────────────────────────────────────────────────────────┐
│                        DEVICE TIER                               │
│   Solar farms, weather stations, unmanned remote devices        │
│   Device A  |  Device B  |  Device N  (any number)             │
└─────────────────────────┬────────────────────────────────────────┘
                          │  POST /monitors  (register)
                          │  POST /monitors/{id}/heartbeat (ping)
                          ▼
┌──────────────────────────────────────────────────────────────────┐
│                 FastAPI Gateway (Python + uvicorn)               │
│                                                                  │
│  ┌──────────────────────────────┐  ┌──────────────────────────┐ │
│  │   routers/monitors.py        │  │  services/scheduler.py   │ │
│  │   POST   /monitors           │  │  Background asyncio task │ │
│  │   GET    /monitors           │  │  Polls every 1s          │ │
│  │   GET    /monitors/{id}      │  │  Checks all deadlines    │ │
│  │   POST   /{id}/heartbeat     │  │  Calls fire_alert()      │ │
│  │   POST   /{id}/pause         │  └──────────┬───────────────┘ │
│  │   DELETE /{id}               │             │ on expire        │
│  └──────────────┬───────────────┘  ┌──────────▼───────────────┐ │
│                 │ CRUD             │  services/alerter.py     │ │
│  ┌──────────────▼───────────────┐  │  Logs ALERT JSON         │ │
│  │   store/monitor_store.py     │  │  Sets status = DOWN      │ │
│  │   In-memory dict             │◄─│  Webhook-ready (prod)    │ │
│  │   MonitorEntry dataclass     │  └──────────────────────────┘ │
│  │   asyncio.Lock               │                               │
│  └──────────────────────────────┘                               │
└──────────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌──────────────────────────────────────────────────────────────────┐
│                        ALERT OUTPUT                              │
│   Console log + JSON payload  →  webhook / email in production  │
└──────────────────────────────────────────────────────────────────┘
```

### Monitor State Machine

```
[ACTIVE]  ←──────────── heartbeat resets deadline
    │
    │  deadline reached (no heartbeat)
    │  OR pause called
    ▼
[PAUSED]  ←──────────── heartbeat un-pauses automatically
    │
    │  deadline reached while ACTIVE
    ▼
[DOWN]  (terminal — re-register to resume)
```

---

## Project Structure

```
pulse-check-api/
├── app/
│   ├── main.py                  # FastAPI app, lifespan, watchdog task launch
│   ├── config.py                # Env-based settings (pydantic-settings)
│   ├── models/
│   │   └── monitor.py           # Pydantic request/response schemas
│   ├── routers/
│   │   └── monitors.py          # All /monitors endpoints
│   ├── services/
│   │   ├── scheduler.py         # Background watchdog loop
│   │   └── alerter.py           # Alert firing + DOWN state transition
│   └── store/
│       └── monitor_store.py     # In-memory store: dict + asyncio.Lock
├── tests/
│   └── test_monitors.py         # 18 tests — all user stories + bonus
├── .env.example
├── .gitignore
├── pytest.ini
├── requirements.txt
└── README.md
```

---

## Setup Instructions (Windows Git Bash)

### Prerequisites
- Python 3.11 or later
- pip
- Git Bash

### Step-by-step

```bash
# 1. Clone your forked repository
git clone https://github.com/YOUR_USERNAME/pulse-check-api.git
cd pulse-check-api

# 2. Create and activate virtual environment
python -m venv venv
source venv/Scripts/activate      # Git Bash on Windows
# source venv/bin/activate         # macOS / Linux

# 3. Install dependencies
pip install -r requirements.txt

# 4. Copy environment config (optional)
cp .env.example .env

# 5. Start the server
uvicorn app.main:app --reload --port 8000

# Server at http://localhost:8000
# Swagger docs at http://localhost:8000/docs
```

---

## API Documentation

### `POST /monitors` — Register a monitor

Start tracking a device. The countdown begins immediately.

**Request body:**
```json
{
  "id": "device-123",
  "timeout": 60,
  "alert_email": "admin@critmon.com"
}
```

**curl:**
```bash
curl -X POST http://localhost:8000/monitors \
  -H "Content-Type: application/json" \
  -d '{"id":"device-123","timeout":60,"alert_email":"admin@critmon.com"}'
```

**Response (201 Created):**
```json
{
  "id": "device-123",
  "timeout": 60,
  "alert_email": "admin@critmon.com",
  "status": "ACTIVE",
  "deadline": "2025-07-26T10:01:00+00:00",
  "last_heartbeat": null,
  "created_at": "2025-07-26T10:00:00+00:00"
}
```

---

### `POST /monitors/{id}/heartbeat` — Send a heartbeat

Reset the countdown. Call this before the deadline to prevent an alert.

```bash
curl -X POST http://localhost:8000/monitors/device-123/heartbeat
```

**Response (200 OK):** Same as above with updated `deadline` and `last_heartbeat`.

| Scenario | Status |
|----------|--------|
| Monitor exists + ACTIVE | 200 — deadline reset |
| Monitor exists + PAUSED | 200 — un-paused + deadline reset |
| Monitor not found | 404 |
| Monitor is DOWN | 410 — must re-register |

---

### `POST /monitors/{id}/pause` — Pause monitoring (maintenance mode)

Stops the countdown. No alert will fire while paused. Sending a heartbeat automatically un-pauses.

```bash
curl -X POST http://localhost:8000/monitors/device-123/pause
```

**Response (200 OK):** Monitor object with `status: "PAUSED"`.

---

### `GET /monitors` — List all monitors

```bash
curl http://localhost:8000/monitors
```

Returns all monitors sorted newest-first.

---

### `GET /monitors/{id}` — Get one monitor

```bash
curl http://localhost:8000/monitors/device-123
```

Returns the monitor's current state including status, deadline, and last heartbeat.

---

### `DELETE /monitors/{id}` — Delete a monitor

```bash
curl -X DELETE http://localhost:8000/monitors/device-123
```

```json
{ "message": "Monitor 'device-123' deleted." }
```

---

### `GET /health`

```bash
curl http://localhost:8000/health
```

```json
{ "status": "ok", "timestamp": "2025-07-26T10:00:00+00:00" }
```

---

## Alert Output

When a device misses its deadline the server logs:

```
============================================================
ALERT: Device 'device-123' is down!
  alert_email : admin@critmon.com
  time        : 2025-07-26T10:01:05+00:00
  last_ping   : 2025-07-26T09:59:50+00:00
============================================================
```

And the monitor status transitions to `DOWN`.

---

## Running Tests

```bash
pytest tests/ -v
# Expected: 18 passed
```

Coverage: all 3 user stories, bonus pause/unpause, developer's choice endpoints, edge cases (404, 409, 410, 422).

---

## Design Decisions

### 1. asyncio background task for the watchdog scheduler
The watchdog runs as an `asyncio.create_task()` started in the FastAPI lifespan context. It polls every 1 second (`SCHEDULER_TICK_SECONDS`). Because asyncio is cooperative, the loop only runs between request handlers — it never blocks the API. The 1-second tick means maximum alert latency is ~1 second after deadline, which is acceptable for devices with minute-scale timeouts.

### 2. PENDING-free state design
Unlike the idempotency gateway, monitors don't need a PENDING state — each monitor is independent. The state machine is simple: ACTIVE → PAUSED → DOWN. The watchdog only fires alerts for ACTIVE monitors whose `deadline < now()`, so paused monitors are invisible to it.

### 3. asyncio.Lock for store writes
All writes to the in-memory dict go through `asyncio.Lock`. Since Python asyncio is single-threaded, this only protects across `await` points — but it makes the concurrency contract explicit and future-proofs for thread-pool execution.

### 4. Heartbeat auto-unpauses
When a technician finishes maintenance and the device sends its first heartbeat, the monitor automatically transitions from PAUSED → ACTIVE and resets the deadline. This removes the need for a separate "unpause" endpoint — one fewer API call for the device firmware to implement.

### 5. DOWN is terminal — re-register to resume
Once a device goes DOWN, it cannot be "revived" by a heartbeat. This is intentional: a DOWN alert means human intervention is required. The technician must confirm the device is healthy, fix the issue, and explicitly re-register (DELETE + POST) to resume monitoring. This prevents silent recovery of an actually-still-faulty device.

---

## Developer's Choice Feature: GET /monitors + GET /monitors/{id} + DELETE /monitors/{id}

**What was added:** Three read/management endpoints — list all monitors, inspect one, and delete one.

**Why it matters for a real Fintech/Infra company:**

1. **Observability dashboard.** `GET /monitors` lets ops build a real-time overview of every device's status (ACTIVE / PAUSED / DOWN), countdown remaining, and last heartbeat time. Without it, the system is a black box — you can only tell if something is wrong when an alert fires.

2. **Device decommissioning.** When a solar panel installation is retired, you need to remove its monitor so it doesn't fire false alerts forever. `DELETE /monitors/{id}` closes that loop cleanly.

3. **Debugging.** `GET /monitors/{id}` lets engineers inspect the exact deadline and last heartbeat for a device — essential for diagnosing "why didn't the alert fire?" or "why did it fire early?" without needing database access.

4. **API completeness.** A REST API with only write endpoints isn't really REST — resources should be readable. These endpoints complete the CRUD contract.

---

## Production Upgrade Path

| Component | v1 (this repo) | Production |
|-----------|---------------|------------|
| Monitor store | Python `dict` + `asyncio.Lock` | Redis `HASH` per monitor |
| Timer tracking | Poll all entries every second | Redis sorted set by deadline score |
| Alert delivery | `print()` + `logger.critical()` | Webhook POST + SendGrid email |
| Multi-node | Not supported | Redis shared across all nodes |

The `monitor_store.py` module exposes a clean `get / create / update / delete / get_expired` interface — swapping for Redis requires no changes to the routers or scheduler.