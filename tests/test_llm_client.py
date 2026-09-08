"""Retry behaviour for Anthropic client adapters."""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, Mock, patch

from agents.orchestrator.llm.client import create_message, generate_text


class FakeStream:
    def __init__(self, chunks: list[str], stop_reason: str) -> None:
        self._chunks = chunks
        self._stop_reason = stop_reason

    async def __aenter__(self) -> "FakeStream":
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    @property
    def text_stream(self):  # type: ignore[no-untyped-def]
        async def chunks():  # type: ignore[no-untyped-def]
            for chunk in self._chunks:
                yield chunk

        return chunks()

    async def get_final_message(self) -> dict[str, object]:
        return {"stop_reason": self._stop_reason}


class ProviderError(Exception):
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(f"HTTP {status_code}")


class LLMClientRetryTests(unittest.TestCase):
    def test_generate_text_continues_once_after_a_token_limit(self) -> None:
        client = Mock()
        client.messages.stream = Mock(
            side_effect=[
                FakeStream(["Use a staged rollout, then ver"], "max_tokens"),
                FakeStream(["ify service health."], "end_turn"),
            ]
        )
        streamed: list[str] = []

        with patch("agents.orchestrator.llm.client.get_async_client", return_value=client):
            result = asyncio.run(
                generate_text(
                    model="test-model",
                    system="system",
                    prompt="question",
                    max_tokens=100,
                    on_text=streamed.append,
                    complete_on_token_limit=True,
                )
            )

        self.assertEqual(result, "Use a staged rollout, then verify service health.")
        self.assertEqual("".join(streamed), result)
        self.assertEqual(client.messages.stream.call_count, 2)
        continuation = client.messages.stream.call_args.kwargs["messages"]
        self.assertEqual(continuation[1]["role"], "assistant")
        self.assertIn("Continue exactly where you stopped", continuation[2]["content"])

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
        with patch("agents.orchestrator.llm.client.get_async_client", return_value=client), patch("agents.orchestrator.llm.client.update_current_generation") as telemetry:
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
                "input": 100,
                "output": 10,
                "cache_creation_input_tokens": 90,
                "cache_read_input_tokens": 0,
            }
        )

    def test_create_message_passes_requested_temperature(self) -> None:
        client = Mock()
        client.messages.create = AsyncMock(return_value="response")

        with patch("agents.orchestrator.llm.client.get_async_client", return_value=client):
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

        with patch("agents.orchestrator.llm.client.get_async_client", return_value=client), patch("agents.orchestrator.retry.asyncio.sleep", sleep):
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

        with patch("agents.orchestrator.llm.client.get_async_client", return_value=client), patch("agents.orchestrator.retry.asyncio.sleep", sleep):
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
