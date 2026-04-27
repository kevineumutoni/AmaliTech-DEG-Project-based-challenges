# Idempotency Gateway — The "Pay-Once" Protocol

A production-grade idempotency layer for **FinSafe Transactions Ltd.** built with **Python + FastAPI**.  
Ensures payment requests are processed **exactly once**, no matter how many times a client retries.

---

## Architecture Diagram

```
┌──────────────────────────────────────────────────────────────────┐
│                        CLIENT TIER                               │
│   E-commerce shop / mobile app / third-party system             │
│   Sends:  POST /process-payment + Idempotency-Key header        │
└─────────────────────────┬────────────────────────────────────────┘
                          │
                          ▼
┌──────────────────────────────────────────────────────────────────┐
│                   FASTAPI GATEWAY (Python)                       │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │              Idempotency Decision Tree                  │    │
│  │                                                         │    │
│  │  1. No Idempotency-Key header?  ──► 400 Bad Request     │    │
│  │                                                         │    │
│  │  2. Key NOT in store (MISS)                             │    │
│  │       └─► Set PENDING (atomic)                          │    │
│  │       └─► Call Payment Processor (2s delay)             │    │
│  │       └─► Store result as COMPLETE                      │    │
│  │       └─► Return 201 Created                            │    │
│  │                                                         │    │
│  │  3. Key status = PENDING (in-flight duplicate)          │    │
│  │       └─► Poll until COMPLETE (max 30s)                 │    │
│  │       └─► Return stored result + X-Cache-Hit: true      │    │
│  │                                                         │    │
│  │  4. Key COMPLETE + same body hash                       │    │
│  │       └─► Replay stored response instantly              │    │
│  │       └─► Return 201 + X-Cache-Hit: true                │    │
│  │                                                         │    │
│  │  5. Key COMPLETE + DIFFERENT body hash                  │    │
│  │       └─► 409 Conflict (fraud/error check)              │    │
│  └─────────────────────────────────────────────────────────┘    │
│                                                                  │
│  ┌──────────────────┐        ┌──────────────────────────────┐   │
│  │   Key Store      │        │   Payment Processor          │   │
│  │  (dict + asyncio │        │   (simulated, 2s delay)      │   │
│  │   lock + TTL)    │        │   Returns: charge + UUID     │   │
│  └──────────────────┘        └──────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌──────────────────────────────────────────────────────────────────┐
│                      RESPONSE TO CLIENT                          │
│   201 (new)  │  201 + X-Cache-Hit: true (replay)  │  409/400    │
└──────────────────────────────────────────────────────────────────┘
```

### Key State Machine

```
[NOT FOUND]
     │
     │  First request arrives → set_pending() called BEFORE await
     ▼
 [PENDING]  ◄──── concurrent duplicates poll here
     │
     │  Processor returns
     ▼
 [COMPLETE]
     │
     │  After TTL_HOURS (default 24h)
     ▼
[EXPIRED]  ──► treated as NOT FOUND on next request
```

---

## Project Structure

```
idempotency-gateway/
├── app/
│   ├── main.py              # FastAPI app, lifespan, background cleanup task
│   ├── config.py            # Settings via environment variables (pydantic-settings)
│   ├── routers/
│   │   └── payments.py      # POST /process-payment — full idempotency logic here
│   ├── services/
│   │   └── processor.py     # Simulated payment processor (async, configurable delay)
│   ├── store/
│   │   └── key_store.py     # In-memory store: dict + asyncio.Lock + TTL eviction
│   └── utils/
│       └── hashing.py       # SHA-256 body fingerprinting (sorted-key canonical form)
├── tests/
│   └── test_idempotency.py  # 14 tests — all 5 user stories + race condition + TTL
├── .env.example
├── .gitignore
├── pytest.ini
├── requirements.txt
└── README.md
```

---

## Setup Instructions

### Prerequisites
- Python 3.11 or later
- pip
- Git Bash (Windows) or any terminal

### Step-by-step (Windows Git Bash)

```bash
# 1. Clone your forked repository
git clone https://github.com/YOUR_USERNAME/idempotency-gateway.git
cd idempotency-gateway

# 2. Create and activate a virtual environment
python -m venv venv
source venv/Scripts/activate      # Git Bash on Windows
# source venv/bin/activate         # macOS / Linux

# 3. Install dependencies
pip install -r requirements.txt

# 4. Copy environment config (optional)
cp .env.example .env

# 5. Start the server
uvicorn app.main:app --reload --port 8000

# Server is now running at http://localhost:8000
# Interactive docs at http://localhost:8000/docs
```

---

## API Documentation

### `POST /process-payment`

Processes a payment. Idempotent — safe to call multiple times with the same key.

#### Required Header
```
Idempotency-Key: <unique-string>   (max 255 characters)
```

#### Request Body
```json
{
  "amount": 100,
  "currency": "GHS"
}
```

---

### Scenario 1 — First request (new payment, ~2s delay)

```bash
curl -X POST http://localhost:8000/process-payment \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: order-abc123" \
  -d '{"amount": 100, "currency": "GHS"}'
```

**Response:**
```json
HTTP/1.1 201 Created
X-Idempotency-Key-Expires: 2025-07-27T10:00:00+00:00

{
  "status": "Charged 100.0 GHS",
  "transaction_id": "550e8400-e29b-41d4-a716-446655440000",
  "processed_at": "2025-07-26T10:00:00+00:00"
}
```

---

### Scenario 2 — Duplicate request (instant replay)

```bash
curl -X POST http://localhost:8000/process-payment \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: order-abc123" \
  -d '{"amount": 100, "currency": "GHS"}'
```

**Response (no delay, same body):**
```json
HTTP/1.1 201 Created
X-Cache-Hit: true
X-Idempotency-Key-Expires: 2025-07-27T10:00:00+00:00

{
  "status": "Charged 100.0 GHS",
  "transaction_id": "550e8400-e29b-41d4-a716-446655440000",
  "processed_at": "2025-07-26T10:00:00+00:00"
}
```

The `transaction_id` is **identical** — proving no new charge occurred.

---

### Scenario 3 — Same key, different body (fraud/error)

```bash
curl -X POST http://localhost:8000/process-payment \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: order-abc123" \
  -d '{"amount": 500, "currency": "GHS"}'
```

**Response:**
```json
HTTP/1.1 409 Conflict

{
  "detail": "Idempotency key already used for a different request body."
}
```

---

### Scenario 4 — Missing header

```bash
curl -X POST http://localhost:8000/process-payment \
  -H "Content-Type: application/json" \
  -d '{"amount": 100, "currency": "GHS"}'
```

**Response:**
```json
HTTP/1.1 400 Bad Request

{
  "detail": "Idempotency-Key header is required."
}
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

## Response Headers Reference

| Header | When Present | Meaning |
|--------|-------------|---------|
| `X-Cache-Hit: true` | Duplicate / in-flight requests | Response was replayed, not freshly processed |
| `X-Idempotency-Key-Expires` | All successful requests | ISO-8601 timestamp — when this key expires |

---

## Running Tests

```bash
# With virtual env active:
pytest tests/ -v

# Expected output: 14 passed
```

Test coverage:
- US-1: Happy path (new payment processed, correct status/body)
- US-2: Duplicate replay (same body, X-Cache-Hit header, same transaction_id)
- US-3: Conflict check (same key, different body → 409)
- US-4: Missing/invalid header → 400
- US-5: Input validation (negative amount, missing currency → 422)
- Bonus: Race condition — concurrent duplicates produce same transaction_id
- Developer's choice: TTL expiry — expired keys treated as new

---

## Design Decisions

### 1. FastAPI + Pydantic for schema validation
Pydantic automatically validates the request body (e.g., rejects negative amounts, missing fields) and returns clean 422 errors before the idempotency logic even runs. This eliminates a whole class of edge cases.

### 2. SHA-256 with sorted-key canonical JSON for body hashing
Two clients might send `{"amount":100,"currency":"GHS"}` and `{"currency":"GHS","amount":100}` — logically identical but different strings. We sort keys before hashing so these produce the same fingerprint. This prevents false 409 conflicts from key-order differences.

### 3. PENDING state set before `await` — the atomic race condition fix
The key is marked `PENDING` synchronously (no `await`) before `await process_payment(...)` is called. Because Python's asyncio is cooperative (only one coroutine runs at a time, and it only yields at `await` points), no other request can observe the store between the PENDING write and the processor call. This prevents double processing without needing a distributed lock.

### 4. asyncio.Lock for store writes
Even though Python asyncio is single-threaded, we use `asyncio.Lock` to make the concurrency contract explicit and to future-proof for potential thread-pool usage with `run_in_executor`.

### 5. Polling for in-flight requests (Bonus user story)
When Request B arrives while Request A is PENDING, B polls every 100ms. This is simple and correct for a single-node deployment. For multi-node (production), replace with Redis pub/sub: A publishes to a per-key channel when done, B subscribes and wakes up immediately.

---

## Developer's Choice Feature: Key Expiry (TTL)

**What it does:** Every idempotency key automatically expires 24 hours after its first use. Expired keys are treated as missing — a re-submission with the same key after 24h is processed as a new payment.

**Why it matters for a real Fintech:**

1. **Memory / storage bounds.** Without TTL, the key store grows forever. At 1,000 transactions/minute, that's 1.44 million keys/day crashing an in-memory store within hours.

2. **Industry standard.** Stripe uses 24-hour TTL. PayPal uses 45 days. Having a published expiry window sets clear client expectations for retry logic.

3. **Legitimate re-billing.** A customer placing the same £100 order again the next day (same amount, new session) should produce a new charge — TTL makes this work without requiring the client to generate a new key.

4. **Response header transparency.** `X-Idempotency-Key-Expires` tells clients exactly when their retry window closes.

**Implementation:** Two-path eviction — lazy (checked on every `get()`) + periodic (`cleanup_expired()` runs every hour as a background asyncio task). This ensures no false cache hits even if the cleanup hasn't run yet.

---

## Production Upgrade Path

| Component | v1 (this repo) | Production |
|-----------|---------------|------------|
| Key store | `dict` + `asyncio.Lock` | Redis with `SET key value EX ttl NX` |
| Atomicity | asyncio event loop | Redis `SETNX` (atomic set-if-not-exists) |
| In-flight wait | asyncio polling | Redis pub/sub (B subscribes to key channel) |
| TTL cleanup | background task | Redis native TTL (automatic eviction) |
| Multi-node | Not supported | Redis shared across all nodes |

The `key_store.py` module exposes a clean `get / set_pending / set_complete` interface — swapping the underlying store for Redis requires no changes to `payments.py`.