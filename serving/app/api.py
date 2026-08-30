"""Public FastAPI frontend that delegates workflow execution to the orchestrator service."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator

from serving.app.langfuse import observe, update_current_span


LOGGER = logging.getLogger(__name__)
PACKAGE_DIRECTORY = Path(__file__).resolve().parent
STATIC_DIRECTORY = PACKAGE_DIRECTORY / "static"
TEMPLATES_DIRECTORY = PACKAGE_DIRECTORY / "templates"
SAFE_ERROR_MESSAGE = "Something went wrong while processing that question. Please try again."
ORCHESTRATOR_SERVICE_URL_ENV = "ORCHESTRATOR_SERVICE_URL"
AGENT_SERVICE_TIMEOUT_ENV = "AGENT_SERVICE_TIMEOUT_SECONDS"
MAX_QUESTION_LENGTH = 4_000


class AskRequest(BaseModel):
    """A browser question and its optional LangGraph conversation identifier."""

    model_config = ConfigDict(extra="forbid")

    question: str = Field(
        min_length=1,
        max_length=MAX_QUESTION_LENGTH,
        description="A Kubernetes or platform-infrastructure question.",
    )
    thread_id: str | None = Field(
        default=None,
        max_length=128,
        description="A browser-session identifier reused for conversational context.",
    )

    @field_validator("question")
    @classmethod
    def question_must_contain_text(cls, value: str) -> str:
        question = value.strip()
        if not question:
            raise ValueError("question must contain non-whitespace text")
        return question

    @field_validator("thread_id")
    @classmethod
    def normalize_thread_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        thread_id = value.strip()
        return thread_id or None


class SourceResponse(BaseModel):
    """Public, structured provenance. Retrieved evidence text remains server-side."""

    model_config = ConfigDict(extra="ignore")

    id: str
    type: str
    title: str
    source: str | None = None
    section: str | None = None
    url: str | None = None


class AskResponse(BaseModel):
    """The guarded answer returned by the orchestrator."""

    answer: str
    is_relevant: bool = True
    sources: list[SourceResponse] = Field(default_factory=list)


class HealthResponse(BaseModel):
    """A deliberately lightweight Kubernetes probe response."""

    status: str

app = FastAPI(
    title="KubeMind API",
    version="0.1.0",
    description="HTTP interface for the Kubernetes assistant.",
)
app.mount("/static", StaticFiles(directory=STATIC_DIRECTORY), name="static")


def _timeout() -> httpx.Timeout:
    try:
        seconds = float(os.getenv(AGENT_SERVICE_TIMEOUT_ENV, "120"))
    except ValueError as error:
        raise RuntimeError(f"{AGENT_SERVICE_TIMEOUT_ENV} must be a positive number") from error
    if seconds <= 0:
        raise RuntimeError(f"{AGENT_SERVICE_TIMEOUT_ENV} must be a positive number")
    return httpx.Timeout(seconds, connect=min(seconds, 10.0))


@observe(name="api-to-orchestrator", as_type="chain", capture_input=False, capture_output=False)
async def invoke(question: str, *, thread_id: str | None = None) -> dict[str, Any]:
    """Call the private orchestrator without exposing its network contract to browsers."""
    orchestrator_url = os.getenv(ORCHESTRATOR_SERVICE_URL_ENV, "").strip()
    if not orchestrator_url:
        raise RuntimeError(f"{ORCHESTRATOR_SERVICE_URL_ENV} is not configured")
    update_current_span(
        input={"question": question},
        metadata={"thread_id": thread_id or "single-turn", "upstream": "orchestrator"},
    )
    try:
        async with httpx.AsyncClient(timeout=_timeout()) as client:
            response = await client.post(
                f"{orchestrator_url.rstrip('/')}/v1/ask",
                json={"question": question, "thread_id": thread_id},
            )
        response.raise_for_status()
        result = response.json()
    except (httpx.HTTPError, ValueError) as error:
        raise RuntimeError("Orchestrator service failed") from error
    if not isinstance(result, dict):
        raise RuntimeError("Orchestrator service returned a malformed response")
    update_current_span(
        output={
            "is_relevant": result.get("is_relevant"),
            "source_count": len(result.get("sources", [])) if isinstance(result.get("sources"), list) else 0,
        }
    )
    return result


@app.get("/", include_in_schema=False)
async def chat_page() -> FileResponse:
    """Serve the single-page browser interface from the public service."""
    return FileResponse(TEMPLATES_DIRECTORY / "index.html", media_type="text/html")


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Return a dependency-free probe result suitable for Kubernetes checks."""
    return HealthResponse(status="ok")


@app.post("/ask", response_model=AskResponse)
async def ask(request: AskRequest) -> AskResponse:
    """Validate a browser question and delegate AI work to the private orchestrator."""
    try:
        result = await invoke(request.question, thread_id=request.thread_id)
    except Exception:
        LOGGER.exception("Orchestrator failed while serving a browser question")
        raise HTTPException(status_code=500, detail=SAFE_ERROR_MESSAGE) from None

    answer = result.get("answer")
    if not isinstance(answer, str) or not answer.strip():
        LOGGER.error("Orchestrator returned no usable answer")
        raise HTTPException(status_code=502, detail=SAFE_ERROR_MESSAGE)
    is_relevant = result.get("is_relevant")
    return AskResponse(
        answer=answer,
        is_relevant=is_relevant if isinstance(is_relevant, bool) else True,
        sources=_public_sources(result.get("sources")),
    )


def _public_sources(value: object) -> list[SourceResponse]:
    """Keep private evidence text and retrieval metadata out of the HTTP contract."""
    if not isinstance(value, list):
        return []
    sources: list[SourceResponse] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        try:
            sources.append(SourceResponse.model_validate(item))
        except Exception:
            LOGGER.warning("Ignoring malformed evidence metadata returned by orchestrator")
    return sources


def _sse(event: str, payload: dict[str, Any]) -> str:
    """Encode a small, JSON-backed server-sent event."""
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


@app.post("/ask/stream")
async def ask_stream(request: AskRequest) -> StreamingResponse:
    """Return the completed guarded answer over the existing browser SSE contract."""

    async def events() -> Any:
        try:
            result = await invoke(request.question, thread_id=request.thread_id)
            answer = result.get("answer")
            if not isinstance(answer, str) or not answer.strip():
                raise RuntimeError("Orchestrator returned no usable answer")
            yield _sse("replace", {"answer": answer})
            sources = _public_sources(result.get("sources"))
            yield _sse("sources", {"sources": [source.model_dump(mode="json") for source in sources]})
            yield _sse("done", {})
        except Exception:
            LOGGER.exception("Orchestrator failed while streaming a browser question")
            yield _sse("error", {"message": SAFE_ERROR_MESSAGE})

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
