"""Optional, non-blocking Langfuse instrumentation for the agent workflow."""

from __future__ import annotations

import logging
import os
from contextlib import ExitStack, contextmanager
from collections.abc import Callable
from collections.abc import Iterator
from typing import Any, TypeVar

from dotenv import load_dotenv


load_dotenv()
LOGGER = logging.getLogger(__name__)
Function = TypeVar("Function", bound=Callable[..., Any])
_get_client: Callable[[], Any] | None = None
_propagate_attributes: Callable[..., Any] | None = None


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
        from langfuse import propagate_attributes as _propagate_attributes
    except ImportError:
        LOGGER.debug("Langfuse credentials are configured, but the langfuse package is not installed")


@contextmanager
def request_trace(
    request_id: str,
    *,
    name: str,
    component: str,
    trace_name: str | None = None,
    input: Any | None = None,
    session_id: str | None = None,
    tags: list[str] | None = None,
) -> Iterator[Any | None]:
    """Create a deterministic request trace and propagate correlation attributes.

    The same request ID produces the same trace ID in each Langfuse project. This
    keeps independently deployed services correlated without coupling telemetry
    failures to the request path.
    """
    if _get_client is None or _propagate_attributes is None:
        yield None
        return

    stack = ExitStack()
    try:
        client = _get_client()
        trace_id = client.create_trace_id(seed=request_id)
        span = stack.enter_context(
            client.start_as_current_observation(
                trace_context={"trace_id": trace_id},
                name=name,
                as_type="agent",
                input=input,
                metadata={"request_id": request_id, "component": component},
            )
        )
        stack.enter_context(
            _propagate_attributes(
                session_id=session_id,
                metadata={"request_id": request_id, "component": component},
                tags=tags or [component],
                trace_name=trace_name or name,
            )
        )
    except Exception:  # pragma: no cover - telemetry must never affect the workflow
        LOGGER.debug("Unable to start the Langfuse request trace", exc_info=True)
        stack.close()
        yield None
        return

    try:
        yield span
    finally:
        try:
            stack.close()
        except Exception:  # pragma: no cover - telemetry must never affect the workflow
            LOGGER.debug("Unable to close the Langfuse request trace", exc_info=True)


def update_trace_span(span: Any | None, **attributes: Any) -> None:
    """Update a request root observation without making telemetry a dependency."""
    if span is None:
        return
    try:
        span.update(**attributes)
    except Exception:  # pragma: no cover - telemetry must never affect the workflow
        LOGGER.debug("Unable to update the Langfuse request trace", exc_info=True)


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


def score_current_trace(name: str, value: float, *, metadata: dict[str, Any] | None = None) -> None:
    """Attach an evaluation metric to the active trace when tracing is enabled."""
    if _get_client is None:
        return
    try:
        _get_client().score_current_trace(name=name, value=value, data_type="NUMERIC", metadata=metadata)
    except Exception:  # pragma: no cover - telemetry must never affect the workflow
        LOGGER.debug("Unable to score the current Langfuse trace", exc_info=True)


def flush_traces() -> None:
    """Flush queued events for short-lived CLI executions."""
    if _get_client is None:
        return
    try:
        _get_client().flush()
    except Exception:  # pragma: no cover - telemetry must never affect the workflow
        LOGGER.debug("Unable to flush Langfuse traces", exc_info=True)
