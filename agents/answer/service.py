"""Private FastAPI application for the grounded answer-generation agent."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse

from agents.answer.agent import answer
from agents.orchestrator.shared.contracts import AnswerRequest, AnswerResponse, transport_state
from serving.app.langfuse import request_trace, update_trace_span

app = FastAPI(title="KubeMind answer agent", docs_url=None, redoc_url=None)


def _sse(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


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
        with request_trace(
            request.request_id or "answer-uncorrelated",
            name="kubemind-answer-request",
            component="answer",
            trace_name="kubemind-request",
            input={"question": request.question},
            tags=["agents", "answer"],
        ) as trace:
            result = await answer(state)
            update_trace_span(trace, output={"answer": result.get("answer")})
    except Exception:
        raise _internal_error() from None
    answer_text = result.get("answer")
    sources = result.get("sources")
    if not isinstance(answer_text, str) or not isinstance(sources, list):
        raise _internal_error()
    if not all(isinstance(source, Mapping) for source in sources):
        raise _internal_error()
    return AnswerResponse(answer=answer_text, sources=[dict(source) for source in sources])


@app.post("/v1/answer/stream")
async def stream_answer(request: AnswerRequest) -> StreamingResponse:
    """Stream guarded answer deltas, with a final replacement when required."""

    async def events() -> Any:
        queue: asyncio.Queue[str | None] = asyncio.Queue()

        async def emit_delta(text: str) -> None:
            await queue.put(_sse("delta", {"text": text}))

        async def emit_replace(answer_text: str) -> None:
            await queue.put(_sse("replace", {"answer": answer_text}))

        async def run() -> None:
            state: dict[str, object] = {
                **transport_state(request.question, request.history),
                "tool_results": request.tool_results,
                "context": request.context,
                "sources": request.sources,
            }
            try:
                with request_trace(
                    request.request_id or "answer-uncorrelated",
                    name="kubemind-answer-request",
                    component="answer",
                    trace_name="kubemind-request",
                    input={"question": request.question},
                    tags=["agents", "answer", "sse"],
                ) as trace:
                    result = await answer(
                        state,
                        config={
                            "configurable": {
                                "answer_stream_handler": emit_delta,
                                "answer_stream_reset_handler": emit_replace,
                            }
                        },
                    )
                    update_trace_span(trace, output={"answer": result.get("answer")})
                answer_text, sources = result.get("answer"), result.get("sources")
                if not isinstance(answer_text, str) or not isinstance(sources, list):
                    raise RuntimeError("Answer agent returned malformed state")
                await queue.put(_sse("done", {"answer": answer_text, "sources": sources}))
            except Exception:
                await queue.put(_sse("error", {"message": "Agent stage could not complete the request."}))
            finally:
                await queue.put(None)

        task = asyncio.create_task(run())
        while (event := await queue.get()) is not None:
            yield event
        await task

    return StreamingResponse(events(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})
