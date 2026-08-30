"""Conversation-history helpers shared by retrieval, answer, and orchestration."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any, Literal, TypedDict

from guardrails import classify_kubernetes_relevance
from agents.orchestrator.llm.client import cached_text_block


MAX_HISTORY_MESSAGES = 6
_FOLLOW_UP_MARKERS = frozenset({"it", "this", "that", "one", "ones", "they", "them", "there", "then", "also", "instead"})
_FOLLOW_UP_PREFIX = re.compile(
    r"^(?:what|when|where|why|how|would|should|can|could|do|does|is|are|show|and)\b", re.IGNORECASE
)


class PromptTextBlock(TypedDict):
    """One text block in a structured LLM prompt."""

    type: Literal["text"]
    text: str


Prompt = str | list[PromptTextBlock]


_SOURCE_ONLY_FOLLOW_UP = re.compile(
    r"^(?:"
    r"source(?:s)?|"
    r"where(?:'s|s|\s+is|\s+did)?\s*(?:ur|your|the)?\s*(?:source|sources|docs?|documentation)(?:\s+(?:from|for|on))?|"
    r"which\s+(?:docs?|documentation|sources?)|"
    r"where\s+did\s+(?:you|u)\s+get\s+(?:that|this|it)(?:\s+from)?"
    r")\??$",
    re.IGNORECASE,
)


def recent_conversation(state: Mapping[str, Any], *, max_messages: int = MAX_HISTORY_MESSAGES) -> str:
    """Format only preceding turns; the current question remains a separate prompt suffix."""
    messages = state.get("messages")
    if not isinstance(messages, Sequence) or isinstance(messages, (str, bytes)):
        return ""
    current_question = state.get("question")
    items = list(messages)
    if isinstance(current_question, str) and items and _message_text(items[-1]).strip() == current_question.strip():
        items.pop()
    lines: list[str] = []
    for message in items[-max_messages:]:
        text = _message_text(message).strip()
        if not text:
            continue
        role = _message_role(message)
        label = "Assistant" if role in {"ai", "assistant"} else "User"
        lines.append(f"{label}: {text}")
    return "\n".join(lines)


def prompt_with_history(history: str, current_content: str) -> Prompt:
    """Place cacheable prior context before the changing current-turn content."""
    if not history:
        return current_content
    return [
        cached_text_block(f"Recent conversation for context only:\n{history}"),
        PromptTextBlock(type="text", text=current_content),
    ]


def is_contextual_kubernetes_follow_up(question: str, state: Mapping[str, Any]) -> bool:
    """Allow concise referential follow-ups only after a relevant session conversation."""
    history = recent_conversation(state)
    normalized_question = question.strip()
    words = re.findall(r"[a-z0-9]+", normalized_question.casefold())
    if not history or not words or len(words) > 20:
        return False
    current = classify_kubernetes_relevance(normalized_question)
    if "clearly_irrelevant" in current.matched_domains:
        return False
    previous = classify_kubernetes_relevance(history)
    if not previous.allowed:
        return False
    return (
        is_source_only_follow_up(normalized_question)
        or bool(set(words) & _FOLLOW_UP_MARKERS)
        or bool(_FOLLOW_UP_PREFIX.match(normalized_question))
    )


def is_source_only_follow_up(question: str) -> bool:
    """Recognise requests that can return the preceding turn's stored evidence."""
    return bool(_SOURCE_ONLY_FOLLOW_UP.match(question.strip()))


def _message_role(message: object) -> str:
    if isinstance(message, Mapping):
        return str(message.get("type") or message.get("role") or "")
    return str(getattr(message, "type", "") or getattr(message, "role", ""))


def _message_text(message: object) -> str:
    content = message.get("content") if isinstance(message, Mapping) else getattr(message, "content", "")
    if isinstance(content, str):
        return content
    if not isinstance(content, Sequence) or isinstance(content, (str, bytes)):
        return ""
    parts: list[str] = []
    for block in content:
        text = block.get("text") if isinstance(block, Mapping) else getattr(block, "text", None)
        if isinstance(text, str):
            parts.append(text)
    return "".join(parts)
