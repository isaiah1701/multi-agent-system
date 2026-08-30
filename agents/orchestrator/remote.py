"""Optional HTTP adapters for independently deployed agent stages."""

import os
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

import httpx
from langchain_core.messages import AIMessage
from langchain_core.runnables.config import RunnableConfig

from agents.orchestrator.shared.conversation import MAX_HISTORY_MESSAGES

if TYPE_CHECKING:
    from agents.orchestrator.orchestrator import AgentState


RETRIEVER_AGENT_URL_ENV = "RETRIEVER_AGENT_URL"
ANSWER_AGENT_URL_ENV = "ANSWER_AGENT_URL"
AGENT_SERVICE_TIMEOUT_ENV = "AGENT_SERVICE_TIMEOUT_SECONDS"


def remote_agents_configured() -> bool:
    """Use service boundaries only when both internal agent URLs are configured."""
    retrieval_url = os.getenv(RETRIEVER_AGENT_URL_ENV, "").strip()
    answer_url = os.getenv(ANSWER_AGENT_URL_ENV, "").strip()
    if bool(retrieval_url) != bool(answer_url):
        raise RuntimeError(
            f"{RETRIEVER_AGENT_URL_ENV} and {ANSWER_AGENT_URL_ENV} must be configured together"
        )
    return bool(retrieval_url)


def _timeout() -> httpx.Timeout:
    try:
        seconds = float(os.getenv(AGENT_SERVICE_TIMEOUT_ENV, "120"))
    except ValueError as error:
        raise RuntimeError(f"{AGENT_SERVICE_TIMEOUT_ENV} must be a positive number") from error
    if seconds <= 0:
        raise RuntimeError(f"{AGENT_SERVICE_TIMEOUT_ENV} must be a positive number")
    return httpx.Timeout(seconds, connect=min(seconds, 10.0))


def _message_text(message: object) -> str:
    content = message.get("content") if isinstance(message, Mapping) else getattr(message, "content", "")
    if isinstance(content, str):
        return content
    if not isinstance(content, Sequence) or isinstance(content, (str, bytes)):
        return ""
    return "".join(
        str(block.get("text", ""))
        if isinstance(block, Mapping)
        else str(getattr(block, "text", ""))
        for block in content
    )


def _message_role(message: object) -> str:
    value = message.get("type") if isinstance(message, Mapping) else getattr(message, "type", None)
    if value is None:
        value = message.get("role") if isinstance(message, Mapping) else getattr(message, "role", "")
    return "assistant" if str(value).casefold() in {"ai", "assistant"} else "user"


def history_for_transport(state: "AgentState") -> list[dict[str, str]]:
    """Keep the small existing history window and avoid serializing LangChain objects."""
    messages = state.get("messages")
    if not isinstance(messages, Sequence) or isinstance(messages, (str, bytes)):
        return []
    question = state.get("question")
    items = list(messages)
    if isinstance(question, str) and items and _message_text(items[-1]).strip() == question.strip():
        items.pop()
    history: list[dict[str, str]] = []
    for message in items[-MAX_HISTORY_MESSAGES:]:
        content = _message_text(message).strip()
        if content:
            history.append({"role": _message_role(message), "content": content})
    return history


def _question(state: "AgentState") -> str:
    question = state.get("question")
    if not isinstance(question, str) or not question.strip():
        raise ValueError("A non-empty question is required")
    return question.strip()


async def _post(url: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=_timeout()) as client:
            response = await client.post(f"{url.rstrip('/')}{path}", json=payload)
        response.raise_for_status()
        body = response.json()
    except (httpx.HTTPError, ValueError) as error:
        raise RuntimeError("Remote agent service failed") from error
    if not isinstance(body, dict):
        raise RuntimeError("Remote agent service returned a malformed response")
    return body


async def retrieve_and_context_remote(state: "AgentState") -> dict[str, object]:
    """Call the retrieval service and validate its narrow state contribution."""
    response = await _post(
        os.environ[RETRIEVER_AGENT_URL_ENV],
        "/v1/retrieve",
        {"question": _question(state), "history": history_for_transport(state)},
    )
    tool_results, context, sources = response.get("tool_results"), response.get("context"), response.get("sources")
    if not isinstance(tool_results, list) or not isinstance(context, str) or not isinstance(sources, list):
        raise RuntimeError("Remote retrieval service returned malformed state")
    return {"tool_results": tool_results, "context": context, "sources": sources}


async def answer_remote(
    state: "AgentState", config: RunnableConfig | None = None
) -> dict[str, object]:
    """Call the answer service and preserve LangGraph's local message checkpoint."""
    tool_results = state.get("tool_results")
    sources = state.get("sources")
    context = state.get("context")
    if not isinstance(tool_results, list) or not isinstance(sources, list) or not isinstance(context, str):
        raise RuntimeError("Remote answer service was called without retrieval state")
    response = await _post(
        os.environ[ANSWER_AGENT_URL_ENV],
        "/v1/answer",
        {
            "question": _question(state),
            "history": history_for_transport(state),
            "tool_results": tool_results,
            "context": context,
            "sources": sources,
        },
    )
    answer = response.get("answer")
    returned_sources = response.get("sources")
    if not isinstance(answer, str) or not answer.strip() or not isinstance(returned_sources, list):
        raise RuntimeError("Remote answer service returned malformed state")

    configurable = (config or {}).get("configurable", {}) if isinstance(config, Mapping) else {}
    stream_handler = configurable.get("answer_stream_handler")
    if callable(stream_handler):
        callback_result = stream_handler(answer)
        if hasattr(callback_result, "__await__"):
            await callback_result
    return {"answer": answer, "sources": returned_sources, "messages": [AIMessage(content=answer)]}
