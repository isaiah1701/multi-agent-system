"""Optional, non-blocking Langfuse instrumentation for the agent workflow."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from typing import Any, TypeVar

from dotenv import load_dotenv


load_dotenv()
LOGGER = logging.getLogger(__name__)
Function = TypeVar("Function", bound=Callable[..., Any])
_get_client: Callable[[], Any] | None = None


def _no_op_observe(*_: Any, **__: Any) -> Callable[[Function], Function]:
    """Provide a no-op decorator until Langfuse is installed and configured."""

    def decorator(function: Function) -> Function:
        return function

    return decorator


observe: Callable[..., Any] = _no_op_observe

if os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY"):
    try:
        from langfuse import get_client as _get_client
        from langfuse import observe
    except ImportError:
        LOGGER.debug("Langfuse credentials are configured, but the langfuse package is not installed")


def update_current_span(**attributes: Any) -> None:
    """Add small, curated request fields without allowing telemetry to affect the app."""
    if _get_client is None:
        return
    try:
        _get_client().update_current_span(**attributes)
    except Exception:  # pragma: no cover - telemetry must never affect the workflow
        LOGGER.debug("Unable to update the current Langfuse span", exc_info=True)


def update_current_generation(**attributes: Any) -> None:
    """Add LLM model/input metadata to the active Langfuse generation when enabled."""
    if _get_client is None:
        return
    try:
        _get_client().update_current_generation(**attributes)
    except Exception:  # pragma: no cover - telemetry must never affect the workflow
        LOGGER.debug("Unable to update the current Langfuse generation", exc_info=True)


def flush_traces() -> None:
    """Flush queued events for short-lived CLI executions."""
    if _get_client is None:
        return
    try:
        _get_client().flush()
    except Exception:  # pragma: no cover - telemetry must never affect the workflow
        LOGGER.debug("Unable to flush Langfuse traces", exc_info=True)
