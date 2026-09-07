"""Private FastAPI application that owns the LangGraph orchestration boundary."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse

from agents.orchestrator.orchestrator import invoke
from agents.orchestrator.shared.contracts import OrchestrationRequest, OrchestrationResponse


app = FastAPI(title="KubeMind orchestration agent", docs_url=None, redoc_url=None)


def _sse(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _internal_error() -> HTTPException:
    """Keep graph, retrieval, and provider failures inside the service network."""
    return HTTPException(status_code=502, detail="Orchestration could not complete the request.")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/ask", response_model=OrchestrationResponse)
async def ask(request: OrchestrationRequest) -> OrchestrationResponse:
    """Run one guarded workflow turn for the frontend service."""
    try:
        result = await invoke(request.question, thread_id=request.thread_id, request_id=request.request_id)
    except Exception:
        raise _internal_error() from None

    answer = result.get("answer")
    sources = result.get("sources")
    is_relevant = result.get("is_relevant")
    if not isinstance(answer, str) or not answer.strip() or not isinstance(sources, list):
        raise _internal_error()
    if not all(isinstance(source, Mapping) for source in sources):
        raise _internal_error()
    return OrchestrationResponse(
        answer=answer,
        request_id=str(result["request_id"]),
        is_relevant=is_relevant if isinstance(is_relevant, bool) else True,
        sources=[dict(source) for source in sources],
    )


@app.post("/v1/ask/stream")
async def ask_stream(request: OrchestrationRequest) -> StreamingResponse:
    """Stream graph answer events to the public API service."""

    async def events() -> Any:
        queue: asyncio.Queue[str | None] = asyncio.Queue()

        async def emit_delta(text: str) -> None:
            await queue.put(_sse("delta", {"text": text}))

        async def emit_replace(answer_text: str) -> None:
            await queue.put(_sse("replace", {"answer": answer_text}))

        async def run() -> None:
            try:
                result = await invoke(
                    request.question,
                    thread_id=request.thread_id,
                    request_id=request.request_id,
                    answer_stream_handler=emit_delta,
                    answer_stream_reset_handler=emit_replace,
                )
                answer, sources = result.get("answer"), result.get("sources")
                if not isinstance(answer, str) or not isinstance(sources, list):
                    raise RuntimeError("Orchestrator returned malformed state")
                await queue.put(_sse("sources", {"sources": sources}))
                await queue.put(
                    _sse(
                        "done",
                        {
                            "request_id": str(result["request_id"]),
                            "answer": answer,
                            "is_relevant": result.get("is_relevant", True),
                        },
                    )
                )
            except Exception:
                await queue.put(_sse("error", {"message": "Orchestration could not complete the request."}))
            finally:
                await queue.put(None)

        task = asyncio.create_task(run())
        while (event := await queue.get()) is not None:
            yield event
        await task

    return StreamingResponse(events(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})
