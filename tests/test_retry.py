"""Tests for bounded exponential-backoff helpers."""

from __future__ import annotations

import asyncio
import unittest

from resilience.retry import DEFAULT_BACKOFF_SECONDS, retry_async, retry_sync


class RetryTests(unittest.TestCase):
    def test_sync_retries_with_exponential_delays_then_succeeds(self) -> None:
        attempts = 0
        delays: list[float] = []

        def operation() -> str:
            nonlocal attempts
            attempts += 1
            if attempts < 4:
                raise TimeoutError("temporary failure")
            return "success"

        result = retry_sync(operation, should_retry=lambda error: isinstance(error, TimeoutError), sleep=delays.append)

        self.assertEqual(result, "success")
        self.assertEqual(attempts, 4)
        self.assertEqual(delays, [1.0, 2.0, 4.0])

    def test_sync_stops_after_four_retries(self) -> None:
        delays: list[float] = []

        with self.assertRaisesRegex(TimeoutError, "temporary failure"):
            retry_sync(
                lambda: (_ for _ in ()).throw(TimeoutError("temporary failure")),
                should_retry=lambda error: isinstance(error, TimeoutError),
                sleep=delays.append,
            )

        self.assertEqual(delays, list(DEFAULT_BACKOFF_SECONDS))

    def test_async_retries_with_exponential_delays_then_succeeds(self) -> None:
        attempts = 0
        delays: list[float] = []

        async def operation() -> str:
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise TimeoutError("temporary failure")
            return "success"

        async def sleep(delay: float) -> None:
            delays.append(delay)

        result = asyncio.run(
            retry_async(operation, should_retry=lambda error: isinstance(error, TimeoutError), sleep=sleep)
        )

        self.assertEqual(result, "success")
        self.assertEqual(attempts, 3)
        self.assertEqual(delays, [1.0, 2.0])


if __name__ == "__main__":
    unittest.main()
