"""
Idempotency Gateway — FastAPI entry point
"""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.config import settings
from app.routers.payments import router as payments_router
from app.store.key_store import cleanup_expired


# ── Logging setup ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)


# ── Background cleanup task ───────────────────────────────────────────────────
async def _cleanup_loop():
    """Purge expired idempotency keys every hour."""
    while True:
        await asyncio.sleep(3600)
        purged = await cleanup_expired()
        if purged:
            logging.info(f"Cleanup: purged {purged} expired idempotency keys")


# ── Lifespan ──────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_cleanup_loop())
    logging.info(
        f"Idempotency Gateway started | port={settings.port} "
        f"ttl_hours={settings.ttl_hours} "
        f"processor_delay={settings.processor_delay_seconds}s"
    )
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


# ── App factory ───────────────────────────────────────────────────────────────
app = FastAPI(
    title="Idempotency Gateway",
    description="Pay-once idempotency layer for FinSafe Transactions Ltd.",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(payments_router, tags=["Payments"])


@app.get("/health", tags=["Health"])
async def health():
    from datetime import datetime, timezone
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.exception_handler(404)
async def not_found_handler(request, exc):
    return JSONResponse(status_code=404, content={"error": "Route not found."})