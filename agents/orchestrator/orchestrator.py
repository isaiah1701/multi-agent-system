"""LangGraph construction and CLI for the linear Kubernetes RAG workflow."""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import logging
import sys
from pathlib import Path
from typing import Any, Literal, Required, TypedDict
from types import ModuleType

from langgraph.graph import END, START, StateGraph

from guardrails import is_kubernetes_question


LOGGER = logging.getLogger(__name__)


class AgentState(TypedDict, total=False):
    """Explicit data passed through the guarded tool, context, and answer graph."""

    question: Required[str]
    is_relevant: bool
    tool_results: list[dict[str, Any]]
    context: str
    answer: str


def _load_subagent_module(name: str) -> ModuleType:
    """Load an agent from the repository's intentionally hyphenated directory."""
    package_name = "agents.sub_agents"
    agent_directory = Path(__file__).resolve().parents[1] / "sub-agents"
    if package_name not in sys.modules:
        package = ModuleType(package_name)
        package.__path__ = [str(agent_directory)]
        sys.modules[package_name] = package
    module_name = f"{package_name}.{name}"
    if module_name in sys.modules:
        return sys.modules[module_name]
    specification = importlib.util.spec_from_file_location(module_name, agent_directory / f"{name}.py")
    if specification is None or specification.loader is None:
        raise ImportError(f"Could not load sub-agent module: {name}")
    module = importlib.util.module_from_spec(specification)
    sys.modules[module_name] = module
    specification.loader.exec_module(module)
    return module


# Answer is loaded first because the tool/context node reuses its evidence-formatting helper.
answer = _load_subagent_module("answer").answer
tool_agent = _load_subagent_module("retrieve")
use_tools = tool_agent.use_tools
add_context = tool_agent.add_context


def input_guardrail(state: AgentState) -> dict[str, bool]:
    """Classify relevance locally so rejected questions cannot invoke a model or tool."""
    question = state.get("question")
    if not isinstance(question, str) or not question.strip():
        raise ValueError("A non-empty question is required")
    return {"is_relevant": is_kubernetes_question(question)}


def route_after_guardrail(state: AgentState) -> Literal["use_tools", "reject"]:
    return "use_tools" if state.get("is_relevant") else "reject"


def reject(_: AgentState) -> dict[str, str]:
    return {"answer": "I can only answer Kubernetes and related platform infrastructure questions."}


def build_app() -> Any:
    """Compile the guarded tool-selection → context → answer workflow."""
    graph = StateGraph(AgentState)
    graph.add_node("input_guardrail", input_guardrail)
    graph.add_node("use_tools", use_tools)
    graph.add_node("add_context", add_context)
    graph.add_node("answer", answer)
    graph.add_node("reject", reject)
    graph.add_edge(START, "input_guardrail")
    graph.add_conditional_edges("input_guardrail", route_after_guardrail)
    graph.add_edge("use_tools", "add_context")
    graph.add_edge("add_context", "answer")
    graph.add_edge("answer", END)
    graph.add_edge("reject", END)
    return graph.compile()


app = build_app()


async def invoke(
    question: str, *, answer_stream_handler: Any | None = None
) -> AgentState:
    """Run the reusable async graph for one user question."""
    if not question.strip():
        raise ValueError("A non-empty question is required")
    return await app.ainvoke(
        {"question": question},
        config={"configurable": {"answer_stream_handler": answer_stream_handler}},
    )


def main() -> int:
    """Invoke the workflow from the command line."""
    parser = argparse.ArgumentParser(description="Ask a grounded Kubernetes documentation question.")
    parser.add_argument("question")
    parser.add_argument("--debug", action="store_true", help="Print retrieval and context summaries")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    try:
        printed_token = False

        def print_token(token: str) -> None:
            nonlocal printed_token
            printed_token = True
            print(token, end="", flush=True)

        result = asyncio.run(invoke(args.question, answer_stream_handler=print_token))
    except (ValueError, RuntimeError) as error:
        LOGGER.error("Workflow failed: %s", error)
        return 2
    if printed_token:
        print()
    else:
        print(result["answer"])
    if args.debug:
        print(f"\n[debug] tool results: {len(result.get('tool_results', []))}")
        print(f"[debug] context:\n{result.get('context', '')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
