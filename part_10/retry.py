"""9.1 — Backoff with full jitter. Retry what might succeed next time; never a request the API called invalid."""
import os
import random

from common import APIError

RETRYABLE = {408, 409, 429, 500, 502, 503, 504, 529}
MAX_RETRIES = 4
BASE = float(os.environ.get("RETRY_BASE", 1.0))
CAP = 30.0


def retryable(exc: Exception) -> bool:
    """Rate limits, overload, server errors and dropped connections; a 400 will fail the same way again."""
    return isinstance(exc, APIError) and (exc.status is None or exc.status in RETRYABLE)


def delay(attempt: int, retry_after: float | None = None) -> float:
    """The server's Retry-After if it sent one, else a random wait up to an exponentially growing cap."""
    if retry_after:
        return retry_after
    return random.uniform(0, min(CAP, BASE * 2 ** attempt))
