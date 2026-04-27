"""
Payment Router — POST /process-payment

Idempotency decision tree:
    1. Missing header          → 400
    2. Key NOT FOUND (MISS)    → set PENDING → process → store → 201
    3. Key PENDING (in-flight) → poll until COMPLETE → return result
    4. Key COMPLETE, hash OK   → replay stored response (X-Cache-Hit: true)
    5. Key COMPLETE, hash DIFF → 409 Conflict
"""

import asyncio
import logging
import time

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.config import settings
from app.services.processor import process_payment
from app.store import key_store
from app.utils.hashing import hash_body

router = APIRouter()
logger = logging.getLogger(__name__)


# Request schema 

class PaymentRequest(BaseModel):
    amount: float = Field(..., gt=0, description="Payment amount (must be positive)")
    currency: str = Field(..., min_length=2, max_length=10, description="Currency code e.g. GHS")


# Helpers 

def _log(key: str, event: str, **extra) -> None:
    """Simple structured log — builds a readable line without custom formatters."""
    parts = [f"key={key}", f"event={event}"]
    parts += [f"{k}={v}" for k, v in extra.items()]
    logger.info(" | ".join(parts))


async def _wait_for_completion(key: str):
    """
    Poll the key store until PENDING → COMPLETE, or timeout.
    Returns the completed KeyEntry, None if key expired, or 'TIMEOUT'.
    """
    deadline = time.monotonic() + settings.poll_timeout_seconds
    while time.monotonic() < deadline:
        await asyncio.sleep(settings.poll_interval_seconds)
        entry = await key_store.get(key)
        if entry is None:
            return None
        if entry.status == "COMPLETE":
            return entry
    return "TIMEOUT"


#  Main route 

@router.post("/process-payment", status_code=201)
async def process_payment_route(
    body: PaymentRequest,
    request: Request,
    idempotency_key: str = Header(
        None,
        alias="idempotency-key",
        description="Unique key per logical payment attempt (max 255 chars)",
    ),
):
    #  Guard: header required 
    if not idempotency_key:
        raise HTTPException(
            status_code=400,
            detail="Idempotency-Key header is required.",
        )

    if len(idempotency_key) > 255:
        raise HTTPException(
            status_code=400,
            detail="Idempotency-Key must be 255 characters or fewer.",
        )

    incoming_hash = hash_body(body.model_dump())

    # ── Lookup 
    existing = await key_store.get(idempotency_key)

    # ── CASE A: Key is in-flight (PENDING) 
    if existing and existing.status == "PENDING":
        _log(idempotency_key, "WAITING_FOR_INFLIGHT")
        result = await _wait_for_completion(idempotency_key)

        if result == "TIMEOUT":
            _log(idempotency_key, "POLL_TIMEOUT")
            raise HTTPException(
                status_code=503,
                detail="Request is still processing. Please retry shortly.",
            )

        if result is None:
            existing = None   # key expired during wait — fall through as new
        else:
            _log(idempotency_key, "INFLIGHT_RESOLVED", status_code=result.status_code)
            return JSONResponse(
                status_code=result.status_code,
                content=result.response,
                headers={
                    "X-Cache-Hit": "true",
                    "X-Idempotency-Key-Expires": key_store.get_expiry_header(result),
                },
            )

    #  CASE B: Key exists and is COMPLETE 
    if existing and existing.status == "COMPLETE":

        # Sub-case B1: Different body → fraud / integrity check
        if existing.body_hash != incoming_hash:
            _log(idempotency_key, "HASH_MISMATCH")
            raise HTTPException(
                status_code=409,
                detail="Idempotency key already used for a different request body.",
            )

        # Sub-case B2: Same body → replay stored response instantly
        _log(idempotency_key, "CACHE_HIT", status_code=existing.status_code)
        return JSONResponse(
            status_code=existing.status_code,
            content=existing.response,
            headers={
                "X-Cache-Hit": "true",
                "X-Idempotency-Key-Expires": key_store.get_expiry_header(existing),
            },
        )

    #  CASE C: Key not found (MISS) — process new payment 
    _log(idempotency_key, "CACHE_MISS")

    # Set PENDING BEFORE awaiting the processor.
    await key_store.set_pending(idempotency_key, incoming_hash)

    try:
        result_body = await process_payment(body.amount, body.currency)
        status_code = 201
        completed = await key_store.set_complete(idempotency_key, status_code, result_body)

        _log(
            idempotency_key,
            "PROCESSED",
            status_code=status_code,
            transaction_id=result_body.get("transaction_id"),
        )

        return JSONResponse(
            status_code=status_code,
            content=result_body,
            headers={
                "X-Idempotency-Key-Expires": key_store.get_expiry_header(completed),
            },
        )

    except Exception as exc:
        # Store an error result so any PENDING pollers don't hang forever
        await key_store.set_complete(
            idempotency_key,
            500,
            {"error": "Payment processing failed."},
        )
        _log(idempotency_key, "PROCESSOR_ERROR", error=str(exc))
        raise HTTPException(
            status_code=500,
            detail="Payment processing failed. Please retry.",
        )