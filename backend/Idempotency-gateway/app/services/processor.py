"""
Simulated Payment Processor

In production this would be an HTTP call to a real processor
(Paystack, Flutterwave, Stripe, etc.).

For this challenge it just sleeps for PROCESSOR_DELAY_SECONDS
and returns a charge confirmation with a UUID transaction ID.
"""

import asyncio
import uuid
from datetime import datetime, timezone

from app.config import settings


async def process_payment(amount: float, currency: str) -> dict:
    """
    Simulate processing a payment.

    Returns the response body dict that will be:
    1. Sent to the client on the first request
    2. Replayed verbatim on all subsequent requests with the same key
    """
    # Simulate downstream latency (network + processor compute)
    await asyncio.sleep(settings.processor_delay_seconds)

    return {
        "status": f"Charged {amount} {currency}",
        "transaction_id": str(uuid.uuid4()),
        "processed_at": datetime.now(timezone.utc).isoformat(),
    }