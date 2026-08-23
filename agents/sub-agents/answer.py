"""Grounded final-answer LangGraph node."""

import json
import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING

from langchain_core.runnables.config import RunnableConfig
from llm.client import LLMClientError, generate_text
from llm.config import ANSWER_MAX_TOKENS, ANSWER_MODEL
if TYPE_CHECKING:
    from agents.orchestrator.orchestrator import AgentState


LOGGER = logging.getLogger(__name__)

CONTEXT_SYSTEM_PROMPT = """You are an evidence-processing stage for a Kubernetes knowledge assistant.
Use only the supplied tool evidence. Produce a concise evidence briefing, not a final answer.
Select useful evidence, remove noise, preserve Kubernetes documentation source and section references,
preserve platform-reference URLs, identify relationships, and explicitly note insufficient, conflicting, or failed
evidence. You may identify evidence-supported trade-offs and inferences, but do not add unsupported facts."""

GENERAL_CONTEXT_SYSTEM_PROMPT = """You are a Kubernetes platform-engineering reasoning stage.
No tool evidence is available for this allowed question. Produce a concise general guidance briefing for a stronger
answering model: identify the relevant Kubernetes concepts, practical trade-offs, assumptions, and safe next steps.
Do not claim current/live facts, invent citations, or make organisation-specific assertions. This is not a final answer."""

ANSWER_SYSTEM_PROMPT = """Answer Kubernetes questions using the supplied evidence briefing and tool evidence when available.
Be concise and technically direct. When tool evidence is available, ground factual claims in it and preserve useful
documentation and platform-reference URL attribution. Make nuanced recommendations and trade-off analysis when the
evidence supports them; clearly distinguish documented facts, recommendations, and assumptions.

When no usable tool evidence is available, answer with general Kubernetes and platform-engineering knowledge instead
of refusing. Clearly frame it as general guidance, do not claim current/live facts, and never invent sources, tool
results, or organisation-specific details."""


def format_tool_results(tool_results: list[Mapping[str, object]]) -> str:
    """Render serialisable tool evidence without losing source metadata."""
    if not tool_results:
        return "No tool evidence is available."
    return json.dumps(tool_results, ensure_ascii=False, indent=2, default=str)


async def answer(state: "AgentState", config: RunnableConfig | None = None) -> dict[str, str]:
    """Generate an evidence-backed answer, with a general-knowledge fallback for allowed questions."""
    question = state.get("question")
    if not isinstance(question, str) or not question.strip():
        raise ValueError("A non-empty question is required")
    tool_results = state.get("tool_results", [])
    context = state.get("context")
    if not isinstance(context, str) or not context.strip():
        raise RuntimeError("Answer generation requires context from the preceding node")

    LOGGER.info("Answer generation started")
    prompt = (
        f"Question:\n{question.strip()}\n\nEvidence briefing:\n{context}\n\n"
        f"Tool evidence:\n{format_tool_results(tool_results)}"
    )
    try:
        stream_handler = (config or {}).get("configurable", {}).get("answer_stream_handler")
        final_answer = await generate_text(
            model=ANSWER_MODEL,
            system=ANSWER_SYSTEM_PROMPT,
            prompt=prompt,
            max_tokens=ANSWER_MAX_TOKENS,
            on_text=stream_handler,
        )
    except (LLMClientError, ValueError) as error:
        raise RuntimeError("Answer generation failed") from error
    except Exception as error:
        raise RuntimeError("Answer generation failed") from error
    if not isinstance(final_answer, str) or not final_answer.strip():
        raise RuntimeError("Answer model returned an empty or malformed response")
    LOGGER.info("Answer generation completed")
    return {"answer": final_answer.strip()}
