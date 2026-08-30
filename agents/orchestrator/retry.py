"""Bounded retries owned by the orchestration layer for transient operations."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Sequence
from typing import TypeVar


Result = TypeVar("Result")
DEFAULT_BACKOFF_SECONDS: tuple[float, ...] = (1.0, 2.0, 4.0, 8.0)
RetryPredicate = Callable[[Exception], bool]
RetryObserver = Callable[[Exception, int, float], None]


def retry_sync(
    operation: Callable[[], Result],
    *,
    should_retry: RetryPredicate,
    delays: Sequence[float] = DEFAULT_BACKOFF_SECONDS,
    on_retry: RetryObserver | None = None,
    sleep: Callable[[float], None] | None = None,
) -> Result:
    """Run an operation up to five times with bounded exponential backoff."""
    sleeper = sleep or time.sleep
    for retry_number, delay in enumerate(delays, start=1):
        try:
            return operation()
        except Exception as error:
            if not should_retry(error):
                raise
            if on_retry is not None:
                on_retry(error, retry_number, delay)
            sleeper(delay)
    return operation()


async def retry_async(
    operation: Callable[[], Awaitable[Result]],
    *,
    should_retry: RetryPredicate,
    delays: Sequence[float] = DEFAULT_BACKOFF_SECONDS,
    on_retry: RetryObserver | None = None,
    sleep: Callable[[float], Awaitable[None]] | None = None,
) -> Result:
    """Asynchronously run an operation with bounded exponential backoff."""
    sleeper = sleep or asyncio.sleep
    for retry_number, delay in enumerate(delays, start=1):
        try:
            return await operation()
        except Exception as error:
            if not should_retry(error):
                raise
            if on_retry is not None:
                on_retry(error, retry_number, delay)
            await sleeper(delay)
    return await operation()
