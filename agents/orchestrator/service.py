"""Private FastAPI application that owns the LangGraph orchestration boundary."""

from __future__ import annotations

from collections.abc import Mapping

from fastapi import FastAPI, HTTPException

from agents.orchestrator.orchestrator import invoke
from agents.orchestrator.shared.contracts import OrchestrationRequest, OrchestrationResponse


app = FastAPI(title="KubeMind orchestration agent", docs_url=None, redoc_url=None)


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
        result = await invoke(request.question, thread_id=request.thread_id)
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
        is_relevant=is_relevant if isinstance(is_relevant, bool) else True,
        sources=[dict(source) for source in sources],
    )
