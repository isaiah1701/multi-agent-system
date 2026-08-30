"""Small, versioned-by-code contracts for private agent HTTP calls."""

from __future__ import annotations

from typing import Any, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field, field_validator


MAX_QUESTION_LENGTH = 4_000
MAX_HISTORY_MESSAGES = 6
MAX_CONTEXT_LENGTH = 20_000
MAX_EVIDENCE_ITEMS = 20


class TransportMessage(TypedDict):
    """Serialized conversation message passed between private services."""
    role: Literal["user", "assistant"]
    content: str


class TransportState(TypedDict):
    """Framework-neutral state passed to retrieval and answer services."""
    question: str
    messages: list[TransportMessage]


class ConversationMessage(BaseModel):
    """A bounded, transport-safe representation of one prior conversation turn."""

    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=8_000)


class RetrievalRequest(BaseModel):
    """The state owned by the orchestrator that retrieval needs for one turn."""

    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=MAX_QUESTION_LENGTH)
    history: list[ConversationMessage] = Field(default_factory=list, max_length=MAX_HISTORY_MESSAGES)

    @field_validator("question")
    @classmethod
    def question_must_contain_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("question must contain non-whitespace text")
        return value


class RetrievalResponse(BaseModel):
    """The retrieval service's narrow contribution to the graph state."""

    model_config = ConfigDict(extra="forbid")

    tool_results: list[dict[str, Any]]
    context: str
    sources: list[dict[str, Any]]


class AnswerRequest(RetrievalRequest):
    """The evidence packet consumed by the answer service."""

    tool_results: list[dict[str, Any]] = Field(max_length=MAX_EVIDENCE_ITEMS)
    context: str = Field(min_length=1, max_length=MAX_CONTEXT_LENGTH)
    sources: list[dict[str, Any]] = Field(max_length=MAX_EVIDENCE_ITEMS)


class AnswerResponse(BaseModel):
    """The only fields returned from answer generation."""

    model_config = ConfigDict(extra="forbid")

    answer: str
    sources: list[dict[str, Any]]


class OrchestrationRequest(BaseModel):
    """The small browser-facing request owned by the orchestration service."""

    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=MAX_QUESTION_LENGTH)
    thread_id: str | None = Field(default=None, max_length=128)

    @field_validator("question")
    @classmethod
    def question_must_contain_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("question must contain non-whitespace text")
        return value

    @field_validator("thread_id")
    @classmethod
    def normalize_thread_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        thread_id = value.strip()
        return thread_id or None


class OrchestrationResponse(BaseModel):
    """The narrow response returned by the private orchestration service."""

    model_config = ConfigDict(extra="forbid")

    answer: str
    is_relevant: bool = True
    sources: list[dict[str, Any]] = Field(default_factory=list)


def transport_state(question: str, history: list[ConversationMessage]) -> TransportState:
    """Keep private services independent of serialized LangChain message classes."""
    return {"question": question, "messages": [message.model_dump() for message in history]}
