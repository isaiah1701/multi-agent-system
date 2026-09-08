"""Shared, lazy Anthropic client and minimal text-completion adapter."""

from __future__ import annotations

import logging
import os
from collections.abc import Awaitable, Callable
from inspect import isawaitable
from typing import Any

from dotenv import load_dotenv

from agents.orchestrator.llm.config import PROMPT_CACHING_ENABLED
from serving.app.langfuse import observe, update_current_generation
from agents.orchestrator.retry import DEFAULT_BACKOFF_SECONDS, retry_async


class LLMClientError(RuntimeError):
    """Raised when the Anthropic client or a text response is unusable."""


_async_client: Any | None = None
TokenHandler = Callable[[str], Awaitable[None] | None]
LOGGER = logging.getLogger(__name__)
_CONTINUE_AFTER_TOKEN_LIMIT = (
    "Continue exactly where you stopped. Finish the answer concisely without repeating any text or adding a new "
    "introduction."
)


def cached_text_block(text: str) -> dict[str, object]:
    """Return an Anthropic cache breakpoint for a stable text prefix when enabled."""
    block: dict[str, object] = {"type": "text", "text": text}
    if PROMPT_CACHING_ENABLED:
        block["cache_control"] = {"type": "ephemeral"}
    return block


def _cached_system(system: str) -> str | list[dict[str, object]]:
    return [cached_text_block(system)] if PROMPT_CACHING_ENABLED else system


def _cached_tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    if not tools:
        return tools
    prepared = [dict(tool) for tool in tools]
    # Anthropic caches the request prefix through the final marked tool.
    if PROMPT_CACHING_ENABLED:
        prepared[-1]["cache_control"] = {"type": "ephemeral"}
    return prepared


def _usage_details(response: object) -> dict[str, int]:
    usage = response.get("usage") if isinstance(response, dict) else getattr(response, "usage", None)
    if usage is None:
        return {}
    details: dict[str, int] = {}
    fields = (
        ("input_tokens", "input"),
        ("output_tokens", "output"),
        ("cache_creation_input_tokens", "cache_creation_input_tokens"),
        ("cache_read_input_tokens", "cache_read_input_tokens"),
    )
    for source_field, langfuse_field in fields:
        value = usage.get(source_field) if isinstance(usage, dict) else getattr(usage, source_field, None)
        if isinstance(value, int):
            details[langfuse_field] = value
    return details


def _record_usage(response: object) -> None:
    details = _usage_details(response)
    if details:
        update_current_generation(usage_details=details)


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


@observe(name="anthropic-stream", as_type="generation", capture_input=False)
async def generate_text(
    *,
    model: str,
    system: str,
    prompt: str | list[dict[str, object]],
    max_tokens: int,
    observation_name: str = "anthropic-stream",
    on_text: TokenHandler | None = None,
    complete_on_token_limit: bool = False,
) -> str:
    """Stream an Anthropic response and optionally finish one token-limited answer."""
    update_current_generation(
        name=observation_name,
        model=model,
        input={"system": system, "prompt": prompt},
        metadata={
            "max_tokens": str(max_tokens),
            "streaming": "true",
            "complete_on_token_limit": str(complete_on_token_limit).lower(),
        },
    )
    emitted_text = False

    async def request() -> str:
        nonlocal emitted_text
        parts: list[str] = []
        messages: list[dict[str, object]] = [{"role": "user", "content": prompt}]
        continuation_available = complete_on_token_limit
        is_continuation = False
        while True:
            final_message: object | None = None
            try:
                async with get_async_client().messages.stream(
                    model=model,
                    max_tokens=max_tokens,
                    system=_cached_system(system),
                    messages=messages,
                ) as stream:
                    async for text in stream.text_stream:
                        emitted_text = True
                        parts.append(text)
                        if on_text is not None:
                            callback_result = on_text(text)
                            if isawaitable(callback_result):
                                await callback_result
                    get_final_message = getattr(stream, "get_final_message", None)
                    if callable(get_final_message):
                        final_message = await get_final_message()
                        _record_usage(final_message)
            except Exception:
                # The caller can trim and safely replace an already-streamed
                # partial answer if the optional continuation itself fails.
                if is_continuation and parts:
                    LOGGER.warning("Token-limit continuation failed; returning the safe completed prefix")
                    break
                raise

            stop_reason = (
                final_message.get("stop_reason")
                if isinstance(final_message, dict)
                else getattr(final_message, "stop_reason", None)
            )
            if stop_reason != "max_tokens" or not continuation_available:
                break
            continuation_available = False
            is_continuation = True
            draft = "".join(parts)
            messages = [
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": draft},
                {"role": "user", "content": _CONTINUE_AFTER_TOKEN_LIMIT},
            ]
        text = "".join(parts).strip()
        if not text:
            raise LLMClientError("Anthropic returned no text content")
        return text

    try:
        return await retry_async(
            request,
            should_retry=lambda error: not emitted_text and _is_retryable_request_error(error),
            on_retry=_log_retry,
        )
    except LLMClientError:
        raise
    except Exception as error:  # pragma: no cover - network/API dependent
        raise _request_error(model, error) from error


def _request_error(model: str, error: Exception) -> LLMClientError:
    """Map provider failures to the project-wide client error type."""
    if getattr(error, "status_code", None) == 404:
        return LLMClientError(
            f"Anthropic model {model!r} was not found or is unavailable to this API key. "
            "Set CONTEXT_MODEL or ANSWER_MODEL in .env to an enabled model ID."
        )
    return LLMClientError("Anthropic request failed")


def _is_retryable_request_error(error: Exception) -> bool:
    """Retry provider timeouts, rate limits, and server errors, but never client errors."""
    status_code = getattr(error, "status_code", None)
    if isinstance(status_code, int):
        return status_code in {408, 425, 429} or 500 <= status_code <= 599
    return isinstance(error, (TimeoutError, ConnectionError, OSError))


def _log_retry(error: Exception, retry_number: int, delay: float) -> None:
    LOGGER.warning(
        "Anthropic request failed (%s); retry %d/%d in %s seconds",
        error,
        retry_number,
        len(DEFAULT_BACKOFF_SECONDS),
        delay,
    )


@observe(name="anthropic-tool-selection", as_type="generation", capture_output=False)
async def create_message(
    *,
    model: str,
    system: str,
    messages: list[dict[str, Any]],
    max_tokens: int,
    observation_name: str = "anthropic-message",
    tools: list[dict[str, Any]] | None = None,
    temperature: float | None = None,
) -> Any:
    """Create a native Anthropic message, optionally allowing the registered tools."""
    update_current_generation(
        name=observation_name,
        model=model,
        metadata={"max_tokens": str(max_tokens), "tool_count": str(len(tools or []))},
    )
    request: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "system": _cached_system(system),
        "messages": messages,
    }
    if tools:
        request["tools"] = _cached_tools(tools)
    if temperature is not None:
        request["temperature"] = temperature
    try:
        response = await retry_async(
            lambda: get_async_client().messages.create(**request),
            should_retry=_is_retryable_request_error,
            on_retry=_log_retry,
        )
        _record_usage(response)
        return response
    except LLMClientError:
        raise
    except Exception as error:  # pragma: no cover - network/API dependent
        raise _request_error(model, error) from error
