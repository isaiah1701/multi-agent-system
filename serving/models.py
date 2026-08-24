"""Pydantic models used at the HTTP boundary."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator


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


class AskResponse(BaseModel):
    """The answer produced by the existing LangGraph workflow."""

    answer: str
    is_relevant: bool = True


class HealthResponse(BaseModel):
    """A deliberately lightweight Kubernetes probe response."""

    status: str
