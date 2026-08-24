"""Retry behaviour for Anthropic client adapters."""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, Mock, patch

from llm.client import create_message


class ProviderError(Exception):
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(f"HTTP {status_code}")


class LLMClientRetryTests(unittest.TestCase):
    def test_create_message_marks_stable_system_and_tool_prefixes_for_anthropic_cache(self) -> None:
        client = Mock()
        client.messages.create = AsyncMock(
            return_value={
                "content": [],
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 10,
                    "cache_creation_input_tokens": 90,
                    "cache_read_input_tokens": 0,
                },
            }
        )
        with patch("llm.client.get_async_client", return_value=client), patch("llm.client.update_current_generation") as telemetry:
            asyncio.run(
                create_message(
                    model="test-model",
                    system="stable instructions",
                    messages=[{"role": "user", "content": "new question"}],
                    max_tokens=100,
                    tools=[{"name": "first"}, {"name": "last"}],
                )
            )

        request = client.messages.create.call_args.kwargs
        self.assertEqual(request["system"][0]["cache_control"], {"type": "ephemeral"})
        self.assertEqual(request["tools"][-1]["cache_control"], {"type": "ephemeral"})
        telemetry.assert_any_call(
            usage_details={
                "input_tokens": 100,
                "output_tokens": 10,
                "cache_creation_input_tokens": 90,
                "cache_read_input_tokens": 0,
            }
        )

    def test_create_message_passes_requested_temperature(self) -> None:
        client = Mock()
        client.messages.create = AsyncMock(return_value="response")

        with patch("llm.client.get_async_client", return_value=client):
            asyncio.run(
                create_message(
                    model="test-model",
                    system="system",
                    messages=[{"role": "user", "content": "hello"}],
                    max_tokens=100,
                    temperature=0.0,
                )
            )

        self.assertEqual(client.messages.create.call_args.kwargs["temperature"], 0.0)

    def test_create_message_retries_transient_provider_errors(self) -> None:
        client = Mock()
        client.messages.create = AsyncMock(side_effect=[ProviderError(500), "response"])
        sleep = AsyncMock()

        with patch("llm.client.get_async_client", return_value=client), patch("resilience.retry.asyncio.sleep", sleep):
            result = asyncio.run(
                create_message(
                    model="test-model",
                    system="system",
                    messages=[{"role": "user", "content": "hello"}],
                    max_tokens=100,
                )
            )

        self.assertEqual(result, "response")
        self.assertEqual(client.messages.create.await_count, 2)
        sleep.assert_awaited_once_with(1.0)

    def test_create_message_does_not_retry_client_errors(self) -> None:
        client = Mock()
        client.messages.create = AsyncMock(side_effect=ProviderError(400))
        sleep = AsyncMock()

        with patch("llm.client.get_async_client", return_value=client), patch("resilience.retry.asyncio.sleep", sleep):
            with self.assertRaisesRegex(Exception, "Anthropic request failed"):
                asyncio.run(
                    create_message(
                        model="test-model",
                        system="system",
                        messages=[{"role": "user", "content": "hello"}],
                        max_tokens=100,
                    )
                )

        self.assertEqual(client.messages.create.await_count, 1)
        sleep.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
