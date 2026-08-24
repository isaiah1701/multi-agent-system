"""FastAPI routes that expose the existing Kubernetes assistant workflow."""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import suppress
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from agents.orchestrator.orchestrator import invoke
from serving.models import AskRequest, AskResponse, HealthResponse


LOGGER = logging.getLogger(__name__)
PACKAGE_DIRECTORY = Path(__file__).resolve().parent
STATIC_DIRECTORY = PACKAGE_DIRECTORY / "static"
TEMPLATES_DIRECTORY = PACKAGE_DIRECTORY / "templates"
SAFE_ERROR_MESSAGE = "Something went wrong while processing that question. Please try again."

app = FastAPI(
    title="KubeMind API",
    version="0.1.0",
    description="HTTP interface for the existing Kubernetes LangGraph assistant.",
)
app.mount("/static", StaticFiles(directory=STATIC_DIRECTORY), name="static")


@app.get("/", include_in_schema=False)
async def chat_page() -> FileResponse:
    """Serve the single-page browser interface from the Python application."""
    return FileResponse(TEMPLATES_DIRECTORY / "index.html", media_type="text/html")


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Return a dependency-free probe result suitable for Kubernetes checks."""
    return HealthResponse(status="ok")


@app.post("/ask", response_model=AskResponse)
async def ask(request: AskRequest) -> AskResponse:
    """Validate a browser question and delegate all AI work to the orchestrator."""
    try:
        result: dict[str, Any] = await invoke(request.question, thread_id=request.thread_id)
    except Exception:
        # Detailed failures remain in server logs; the browser never receives internals.
        LOGGER.exception("Orchestrator failed while serving a browser question")
        raise HTTPException(status_code=500, detail=SAFE_ERROR_MESSAGE) from None

    answer = result.get("answer")
    if not isinstance(answer, str) or not answer.strip():
        LOGGER.error("Orchestrator returned no usable answer")
        raise HTTPException(status_code=502, detail=SAFE_ERROR_MESSAGE)
    is_relevant = result.get("is_relevant")
    return AskResponse(answer=answer, is_relevant=is_relevant if isinstance(is_relevant, bool) else True)


def _sse(event: str, payload: dict[str, str]) -> str:
    """Encode a small, JSON-backed server-sent event."""
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


@app.post("/ask/stream")
async def ask_stream(request: AskRequest) -> StreamingResponse:
    """Stream guardrail-checked answer fragments over a same-origin SSE response."""
    queue: asyncio.Queue[tuple[str, dict[str, str]]] = asyncio.Queue()
    answer_emitted = False

    async def emit_delta(text: str) -> None:
        nonlocal answer_emitted
        if text:
            answer_emitted = True
            await queue.put(("delta", {"text": text}))

    async def replace_answer(answer: str) -> None:
        nonlocal answer_emitted
        answer_emitted = True
        await queue.put(("replace", {"answer": answer}))

    async def run_workflow() -> None:
        try:
            result: dict[str, Any] = await invoke(
                request.question,
                thread_id=request.thread_id,
                answer_stream_handler=emit_delta,
                answer_stream_reset_handler=replace_answer,
            )
            answer = result.get("answer")
            if not isinstance(answer, str) or not answer.strip():
                raise RuntimeError("Orchestrator returned no usable answer")
            # The scope guardrail routes directly to the graph's reject node,
            # bypassing the answer node and therefore its stream callbacks.
            # It is still a valid assistant answer and must reach the browser.
            if not answer_emitted:
                await replace_answer(answer)
            await queue.put(("done", {}))
        except Exception:
            LOGGER.exception("Orchestrator failed while streaming a browser question")
            await queue.put(("error", {"message": SAFE_ERROR_MESSAGE}))

    task = asyncio.create_task(run_workflow())

    async def events() -> Any:
        try:
            while True:
                event, payload = await queue.get()
                yield _sse(event, payload)
                if event in {"done", "error"}:
                    return
        finally:
            if not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
