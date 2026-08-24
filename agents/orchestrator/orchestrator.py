"""LangGraph construction and CLI for the linear Kubernetes RAG workflow."""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import logging
import sys
from uuid import uuid4
from pathlib import Path
from typing import Annotated, Any, Literal, Required, TypedDict
from types import ModuleType

from langchain_core.messages import BaseMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from agents.conversation import is_contextual_kubernetes_follow_up
from guardrails import is_kubernetes_question
from observability import flush_traces, observe, update_current_span


LOGGER = logging.getLogger(__name__)


class AgentState(TypedDict, total=False):
    """Explicit data passed through the guarded tool, context, and answer graph."""

    question: Required[str]
    messages: Annotated[list[BaseMessage], add_messages]
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


@observe(name="input-guardrail", as_type="guardrail", capture_input=False, capture_output=False)
def input_guardrail(state: AgentState) -> dict[str, bool]:
    """Classify relevance locally so rejected questions cannot invoke a model or tool."""
    question = state.get("question")
    if not isinstance(question, str) or not question.strip():
        raise ValueError("A non-empty question is required")
    return {"is_relevant": is_kubernetes_question(question) or is_contextual_kubernetes_follow_up(question, state)}


def route_after_guardrail(state: AgentState) -> Literal["use_tools", "reject"]:
    return "use_tools" if state.get("is_relevant") else "reject"


def reject(_: AgentState) -> dict[str, str]:
    return {"answer": "I can only answer Kubernetes and related platform infrastructure questions."}


def build_app(*, checkpointer: Any | None = None) -> Any:
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
    return graph.compile(checkpointer=checkpointer)


# Local-only session persistence. Kubernetes replicas need a shared, durable
# LangGraph-supported checkpointer (for example PostgreSQL) before production.
app = build_app(checkpointer=InMemorySaver())


@observe(name="kubernetes-agent-request", as_type="agent", capture_input=False, capture_output=False)
async def invoke(
    question: str,
    *,
    thread_id: str | None = None,
    answer_stream_handler: Any | None = None,
    answer_stream_reset_handler: Any | None = None,
) -> AgentState:
    """Run one turn, restoring prior LangGraph state when the thread ID is reused."""
    if not question.strip():
        raise ValueError("A non-empty question is required")
    resolved_thread_id = thread_id.strip() if isinstance(thread_id, str) and thread_id.strip() else f"single-turn-{uuid4().hex}"
    update_current_span(input={"question": question}, metadata={"thread_id": resolved_thread_id})
    result = await app.ainvoke(
        {"question": question, "messages": [HumanMessage(content=question)]},
        config={
            "configurable": {
                "thread_id": resolved_thread_id,
                "answer_stream_handler": answer_stream_handler,
                "answer_stream_reset_handler": answer_stream_reset_handler,
            }
        },
    )
    update_current_span(
        output={"answer": result.get("answer"), "is_relevant": result.get("is_relevant")}
    )
    return result


def main() -> int:
    """Invoke the workflow from the command line."""
    parser = argparse.ArgumentParser(description="Ask a grounded Kubernetes documentation question.")
    parser.add_argument("question", nargs="?")
    parser.add_argument("--debug", action="store_true", help="Print retrieval and context summaries")
    parser.add_argument("--thread-id", help="Reuse conversation state within this running process")
    parser.add_argument("--chat", action="store_true", help="Start an interactive local session")
    args = parser.parse_args()
    if not args.chat and not args.question:
        parser.error("question is required unless --chat is used")
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

        if args.chat:
            return _chat(args.thread_id)
        result = asyncio.run(invoke(args.question, thread_id=args.thread_id, answer_stream_handler=print_token))
    except (ValueError, RuntimeError) as error:
        LOGGER.error("Workflow failed: %s", error)
        return 2
    finally:
        flush_traces()
    if printed_token:
        print()
    else:
        print(result["answer"])
    if args.debug:
        print(f"\n[debug] tool results: {len(result.get('tool_results', []))}")
        print(f"[debug] context:\n{result.get('context', '')}")
    return 0


def _chat(thread_id: str | None) -> int:
    """Keep one explicit in-memory thread alive for a local terminal conversation."""
    session_id = thread_id.strip() if isinstance(thread_id, str) and thread_id.strip() else f"chat-{uuid4().hex}"
    print(f"Local session {session_id}. Type /exit to finish.")
    while True:
        try:
            question = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if question.casefold() in {"/exit", "/quit", "exit", "quit"}:
            return 0
        if not question:
            continue
        try:
            result = asyncio.run(invoke(question, thread_id=session_id))
        except (ValueError, RuntimeError) as error:
            LOGGER.error("Workflow failed: %s", error)
            continue
        print(f"Assistant: {result['answer']}")


if __name__ == "__main__":
    raise SystemExit(main())
