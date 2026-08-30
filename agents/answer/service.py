"""Private FastAPI application for the grounded answer-generation agent."""

from __future__ import annotations

from collections.abc import Mapping

from fastapi import FastAPI, HTTPException

from agents.answer.agent import answer
from agents.orchestrator.shared.contracts import AnswerRequest, AnswerResponse, transport_state

app = FastAPI(title="KubeMind answer agent", docs_url=None, redoc_url=None)


def _internal_error() -> HTTPException:
    """Do not disclose model or guardrail internals to callers."""
    return HTTPException(status_code=502, detail="Agent stage could not complete the request.")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/answer", response_model=AnswerResponse)
async def generate_answer(request: AnswerRequest) -> AnswerResponse:
    """Generate and guard a final answer from the retrieval evidence packet."""
    state: dict[str, object] = {
        **transport_state(request.question, request.history),
        "tool_results": request.tool_results,
        "context": request.context,
        "sources": request.sources,
    }
    try:
        result = await answer(state)
    except Exception:
        raise _internal_error() from None
    answer_text = result.get("answer")
    sources = result.get("sources")
    if not isinstance(answer_text, str) or not isinstance(sources, list):
        raise _internal_error()
    if not all(isinstance(source, Mapping) for source in sources):
        raise _internal_error()
    return AnswerResponse(answer=answer_text, sources=[dict(source) for source in sources])
