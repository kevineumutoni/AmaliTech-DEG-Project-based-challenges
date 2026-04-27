"""
Pulse-Check API — Dead Man's Switch for remote device monitoring.
"""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.config import settings
from app.routers.monitors import router as monitors_router
from app.services.scheduler import watchdog_loop


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start the watchdog scheduler on startup; cancel on shutdown."""
    task = asyncio.create_task(watchdog_loop())
    logging.info(
        "Pulse-Check API started | port=%d tick=%.1fs",
        settings.port,
        settings.scheduler_tick_seconds,
    )
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    logging.info("Watchdog scheduler stopped.")


app = FastAPI(
    title="Pulse-Check API",
    description="Dead Man's Switch for CritMon remote device monitoring.",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(monitors_router)


@app.get("/health", tags=["Health"])
async def health():
    from datetime import datetime, timezone
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.exception_handler(404)
async def not_found(request, exc):
    return JSONResponse(status_code=404, content={"error": "Route not found."})