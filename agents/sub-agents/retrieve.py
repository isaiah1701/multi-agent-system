"""Tool-selection and evidence-context LangGraph nodes."""

from __future__ import annotations

import inspect
import json
import logging
from collections.abc import Mapping
from typing import Any, TYPE_CHECKING

from agents.sub_agents.answer import CONTEXT_SYSTEM_PROMPT, GENERAL_CONTEXT_SYSTEM_PROMPT, format_tool_results
from llm.client import LLMClientError, create_message, generate_text
from llm.config import CONTEXT_MAX_TOKENS, CONTEXT_MODEL
from tools import PLATFORM_REFERENCE_SEARCH_TOOL, TOOL_FUNCTIONS, TOOL_SCHEMAS


LOGGER = logging.getLogger(__name__)
MAX_TOOL_ROUNDS = 3

TOOL_AGENT_SYSTEM_PROMPT = """You are the tool-selection stage of a Kubernetes knowledge assistant.
Choose only the registered tools needed to gather evidence for the user's Kubernetes question.
Use search_kubernetes_docs for Kubernetes concepts and behaviour, github_kubernetes_lookup for current
kubernetes/kubernetes releases, changelogs, issues, pull requests, or tags, and calculate for precise arithmetic.
For questions naming Kubernetes resources or shorthand such as PDB, HPA, PVC, Pods, or Ingress, call
search_kubernetes_docs before completing whenever local documentation can provide the answer.
For detailed release changes, look up the latest release first, then request its changelog using the returned tag.
Use the server-managed web_search only for nuanced cloud, ecosystem, platform, or current-vendor questions that
the local Kubernetes docs and GitHub tools cannot answer. It is restricted to authoritative documentation domains.
For questions comparing or recommending AWS, Azure, GCP, EKS, ECS, AKS, GKE, infrastructure tooling, or platform
products, you MUST call web_search before completing; do not answer those questions from your own knowledge.
Use multiple tools when the question requires multiple kinds of evidence. Do not use every tool by default.
Never claim that a tool result says more than it does. When you have enough evidence, respond without another tool call."""

if TYPE_CHECKING:
    from agents.orchestrator.orchestrator import AgentState


def _question_from(state: "AgentState") -> str:
    question = state.get("question")
    if not isinstance(question, str) or not question.strip():
        raise ValueError("A non-empty question is required")
    return question.strip()


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _is_tool_failure(output: dict[str, Any]) -> bool:
    return output.get("ok") is False


def _tool_error(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message": message}}


def _server_search_evidence(content: list[Any]) -> dict[str, Any] | None:
    """Extract citable, plain-text evidence from Anthropic's server-side web search blocks."""
    search_blocks = [block for block in content if _field(block, "type") == "web_search_tool_result"]
    if not search_blocks:
        return None

    sources: list[dict[str, str]] = []
    for block in content:
        if _field(block, "type") != "text":
            continue
        for citation in _field(block, "citations", []) or []:
            url = _field(citation, "url")
            if not isinstance(url, str) or not url:
                continue
            sources.append(
                {
                    "url": url,
                    "title": str(_field(citation, "title", "")),
                    "excerpt": str(_field(citation, "cited_text", "")),
                }
            )

    errors = []
    for block in search_blocks:
        result = _field(block, "content")
        if isinstance(result, Mapping) and _field(result, "type") == "web_search_tool_result_error":
            errors.append(str(_field(result, "error_code", "unavailable")))
    if errors:
        return _tool_error("platform_lookup_failed", f"Platform reference search failed: {', '.join(errors)}")

    text = "\n".join(
        _field(block, "text", "")
        for block in content
        if _field(block, "type") == "text" and isinstance(_field(block, "text", ""), str)
    ).strip()
    if not text and not sources:
        return _tool_error("platform_lookup_empty", "Platform reference search returned no usable evidence")
    return {"ok": True, "summary": text, "sources": sources}


async def use_tools(state: "AgentState") -> dict[str, list[dict[str, Any]]]:
    """Let Claude select and execute only registered tools for at most three rounds."""
    question = _question_from(state)
    messages: list[dict[str, Any]] = [{"role": "user", "content": question}]
    tool_results: list[dict[str, Any]] = []

    for round_number in range(1, MAX_TOOL_ROUNDS + 1):
        LOGGER.info("Tool-selection round %d started", round_number)
        try:
            response = await create_message(
                model=CONTEXT_MODEL,
                system=TOOL_AGENT_SYSTEM_PROMPT,
                messages=messages,
                max_tokens=CONTEXT_MAX_TOKENS,
                tools=[*TOOL_SCHEMAS, PLATFORM_REFERENCE_SEARCH_TOOL],
            )
        except (LLMClientError, ValueError) as error:
            raise RuntimeError("Tool-selection model failed") from error
        except Exception as error:
            raise RuntimeError("Tool-selection model failed") from error

        content = _field(response, "content", [])
        if not isinstance(content, list):
            raise RuntimeError("Tool-selection model returned malformed content")
        server_evidence = _server_search_evidence(content)
        if server_evidence is not None:
            tool_results.append({"tool_name": "platform_reference_lookup", "output": server_evidence})
        calls = [block for block in content if _field(block, "type") == "tool_use"]
        if not calls:
            if _field(response, "stop_reason") == "pause_turn":
                messages.append({"role": "assistant", "content": content})
                continue
            LOGGER.info("Tool-selection completed after %d round(s)", round_number)
            break

        messages.append({"role": "assistant", "content": content})
        tool_result_blocks: list[dict[str, Any]] = []
        for call in calls:
            tool_name = _field(call, "name")
            tool_use_id = _field(call, "id")
            arguments = _field(call, "input", {})
            if not isinstance(tool_name, str) or tool_name not in TOOL_FUNCTIONS:
                output = _tool_error("unknown_tool", f"Unsupported tool requested: {tool_name!r}")
            elif not isinstance(arguments, Mapping):
                output = _tool_error("invalid_arguments", "Tool arguments must be an object")
            else:
                try:
                    output = TOOL_FUNCTIONS[tool_name](**dict(arguments))
                    if inspect.isawaitable(output):
                        output = await output
                except (TypeError, ValueError) as error:
                    output = _tool_error("invalid_arguments", str(error))
                except Exception as error:  # Keep one tool failure from crashing the entire agent loop.
                    LOGGER.exception("Tool %s failed", tool_name)
                    output = _tool_error("tool_execution_failed", str(error))

            if not isinstance(output, dict):
                output = _tool_error("malformed_tool_result", "Tool returned a non-object result")
            tool_results.append({"tool_name": tool_name or "unknown", "output": output})
            if isinstance(tool_use_id, str) and tool_use_id:
                tool_result_blocks.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_use_id,
                        "content": json.dumps(output, ensure_ascii=False, default=str),
                        "is_error": _is_tool_failure(output),
                    }
                )
            else:
                LOGGER.warning("Ignoring a malformed tool call without an id")

        if not tool_result_blocks:
            break
        messages.append({"role": "user", "content": tool_result_blocks})
    else:
        LOGGER.warning("Tool-selection stopped after the maximum of %d rounds", MAX_TOOL_ROUNDS)

    return {"tool_results": tool_results}


async def add_context(state: "AgentState") -> dict[str, str]:
    """Use the low-cost model to create a grounded briefing from tool evidence."""
    question = _question_from(state)
    tool_results = state.get("tool_results", [])
    successful_results = [
        result
        for result in tool_results
        if isinstance(result, Mapping)
        and isinstance(result.get("output"), Mapping)
        and result["output"].get("ok") is not False
    ]
    if not successful_results:
        LOGGER.info("No usable tool evidence; generating a general guidance briefing")
        prompt = f"Question:\n{question}\n\nNo tool evidence was returned."
        system = GENERAL_CONTEXT_SYSTEM_PROMPT
    else:
        LOGGER.info("Context generation started for %d tool result(s)", len(successful_results))
        prompt = f"Question:\n{question}\n\nTool evidence:\n{format_tool_results(successful_results)}"
        system = CONTEXT_SYSTEM_PROMPT

    try:
        context = await generate_text(
            model=CONTEXT_MODEL,
            system=system,
            prompt=prompt,
            max_tokens=CONTEXT_MAX_TOKENS,
        )
    except (LLMClientError, ValueError) as error:
        raise RuntimeError("Context generation failed") from error
    except Exception as error:
        raise RuntimeError("Context generation failed") from error
    if not isinstance(context, str) or not context.strip():
        raise RuntimeError("Context model returned an empty or malformed response")
    LOGGER.info("Context generation completed")
    return {"context": context.strip()}
