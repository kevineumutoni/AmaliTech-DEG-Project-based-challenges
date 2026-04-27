import hashlib
import json
from typing import Any


def hash_body(body: dict[str, Any]) -> str:
    """
    Returns a hex SHA-256 digest of the request body.

    We serialize the dict with sorted keys so that
    {"amount": 100, "currency": "GHS"} and
    {"currency": "GHS", "amount": 100}
    produce the SAME hash — correct for idempotency purposes.

    Why sorted keys? JSON serialisation order is implementation-defined.
    Two clients could send logically identical payloads in different key orders.
    Sorting makes hash comparison robust.
    """
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()