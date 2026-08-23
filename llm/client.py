"""Shared, lazy Anthropic client and minimal text-completion adapter."""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from inspect import isawaitable
from typing import Any

from dotenv import load_dotenv


class LLMClientError(RuntimeError):
    """Raised when the Anthropic client or a text response is unusable."""


_async_client: Any | None = None
TokenHandler = Callable[[str], Awaitable[None] | None]


def get_async_client() -> Any:
    """Return the process-wide async Anthropic client without exposing credentials."""
    global _async_client
    if _async_client is None:
        load_dotenv()
        if not os.getenv("ANTHROPIC_API_KEY"):
            raise LLMClientError("ANTHROPIC_API_KEY is not configured")
        try:
            from anthropic import AsyncAnthropic
        except ImportError as error:  # pragma: no cover - environment dependent
            raise LLMClientError(
                "anthropic is required. Install dependencies with: python -m pip install -r requirements.txt"
            ) from error
        _async_client = AsyncAnthropic()
    return _async_client


async def generate_text(
    *,
    model: str,
    system: str,
    prompt: str,
    max_tokens: int,
    on_text: TokenHandler | None = None,
) -> str:
    """Stream an Anthropic response over SSE and return its accumulated text."""
    try:
        parts: list[str] = []
        async with get_async_client().messages.stream(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            async for text in stream.text_stream:
                parts.append(text)
                if on_text is not None:
                    callback_result = on_text(text)
                    if isawaitable(callback_result):
                        await callback_result
    except LLMClientError:
        raise
    except Exception as error:  # pragma: no cover - network/API dependent
        raise _request_error(model, error) from error

    text = "".join(parts).strip()
    if not text:
        raise LLMClientError("Anthropic returned no text content")
    return text


def _request_error(model: str, error: Exception) -> LLMClientError:
    """Map provider failures to the project-wide client error type."""
    if getattr(error, "status_code", None) == 404:
        return LLMClientError(
            f"Anthropic model {model!r} was not found or is unavailable to this API key. "
            "Set CONTEXT_MODEL or ANSWER_MODEL in .env to an enabled model ID."
        )
    return LLMClientError("Anthropic request failed")


async def create_message(
    *,
    model: str,
    system: str,
    messages: list[dict[str, Any]],
    max_tokens: int,
    tools: list[dict[str, Any]] | None = None,
) -> Any:
    """Create a native Anthropic message, optionally allowing the registered tools."""
    request: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": messages,
    }
    if tools:
        request["tools"] = tools
    try:
        return await get_async_client().messages.create(**request)
    except LLMClientError:
        raise
    except Exception as error:  # pragma: no cover - network/API dependent
        raise _request_error(model, error) from error
