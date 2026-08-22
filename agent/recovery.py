"""Retry/recovery policy: only transient failures retry."""
import asyncio
from typing import Tuple

RETRYABLE_MARKERS = ("timeout", "timed out", "rate limit", "429", "network",
                     "temporarily", "502", "503", "connection")


def is_retryable_error(error_text: str) -> bool:
    low = (error_text or "").lower()
    return any(m in low for m in RETRYABLE_MARKERS)


async def with_retry(run, policy_attempts: int = 1, backoff: float = 1.5) -> Tuple[bool, dict]:
    """run() -> (success, info). Retries only when the error is transient."""
    success, info = await run()
    attempts = 0
    while not success and attempts < policy_attempts:
        err = str((info or {}).get("error", "")) if isinstance(info, dict) else str(info)
        if not is_retryable_error(err):
            break
        attempts += 1
        await asyncio.sleep(backoff * attempts)
        success, info = await run()
    return success, info
