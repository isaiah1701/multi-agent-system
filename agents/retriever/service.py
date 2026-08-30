"""Private FastAPI application for the retrieval and evidence-briefing agent."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException

from agents.retriever.agent import add_context, use_tools
from agents.orchestrator.shared.contracts import RetrievalRequest, RetrievalResponse, transport_state

app = FastAPI(title="KubeMind retrieval agent", docs_url=None, redoc_url=None)


def _internal_error() -> HTTPException:
    """Do not disclose retrieval, tool, or model internals to callers."""
    return HTTPException(status_code=502, detail="Agent stage could not complete the request.")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/retrieve", response_model=RetrievalResponse)
async def retrieve(request: RetrievalRequest) -> RetrievalResponse:
    """Run tool selection and evidence briefing for a validated internal request."""
    state = transport_state(request.question, request.history)
    try:
        tool_state = await use_tools(state)
        context_state = await add_context({**state, **tool_state})
    except Exception:
        raise _internal_error() from None
    tool_results = tool_state.get("tool_results")
    context = context_state.get("context")
    sources = context_state.get("sources")
    if not isinstance(tool_results, list) or not isinstance(context, str) or not isinstance(sources, list):
        raise _internal_error()
    return RetrievalResponse(tool_results=tool_results, context=context, sources=sources)
