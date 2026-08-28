"""Retry/recovery policy: only transient failures retry."""
import asyncio
from typing import Tuple

from agent.policies import is_retryable_error, MAX_TOOL_RETRIES

DEFAULT_ATTEMPTS = MAX_TOOL_RETRIES - 1  # initial attempt + up to 2 retries


async def with_retry(run, policy_attempts: int = DEFAULT_ATTEMPTS,
                     backoff: float = 1.5) -> Tuple[bool, dict]:
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
