"""Credit-free checks for LangGraph in-memory conversational sessions."""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from agents.orchestrator.shared.conversation import is_source_only_follow_up
from agents.orchestrator.orchestrator import build_app, input_guardrail


def tool_complete() -> dict[str, object]:
    return {
        "content": [
            {"type": "web_search_tool_result", "content": []},
            {
                "type": "text",
                "text": "Evidence collected.",
                "citations": [
                    {
                        "url": "https://kubernetes.io/docs/concepts/workloads/pods/",
                        "title": "Kubernetes Pods",
                        "cited_text": "Pods are Kubernetes workload units.",
                    }
                ],
            },
        ]
    }


class SessionMemoryTests(unittest.TestCase):
    def _run(self, coroutine: object) -> object:
        return asyncio.run(coroutine)  # type: ignore[arg-type]

    def test_same_thread_keeps_prior_turn_and_separates_current_question(self) -> None:
        app = build_app(checkpointer=InMemorySaver())
        with (
            patch("agents.retriever.agent.create_message", new=AsyncMock(side_effect=[tool_complete(), tool_complete()])),
            patch("agents.retriever.agent.generate_text", new=AsyncMock(side_effect=["first briefing", "second briefing"])),
            patch("agents.answer.agent.generate_text", new=AsyncMock(side_effect=["first answer [1]", "second answer [1]"])) as answer_model,
        ):
            config = {"configurable": {"thread_id": "conversation-a"}}
            self._run(
                app.ainvoke(
                    {"question": "What is a PodDisruptionBudget?", "messages": [HumanMessage(content="What is a PodDisruptionBudget?")]},
                    config=config,
                )
            )
            result = self._run(
                app.ainvoke(
                    {"question": "When should I use one?", "messages": [HumanMessage(content="When should I use one?")]},
                    config=config,
                )
            )

        second_prompt = answer_model.await_args_list[1].kwargs["prompt"]
        self.assertIsInstance(second_prompt, list)
        self.assertIn("What is a PodDisruptionBudget?", second_prompt[0]["text"])
        self.assertIn("first answer [1]", second_prompt[0]["text"])
        self.assertIn("When should I use one?", second_prompt[1]["text"])
        self.assertEqual(len(result["messages"]), 4)

    def test_different_threads_are_isolated(self) -> None:
        app = build_app(checkpointer=InMemorySaver())
        with (
            patch("agents.retriever.agent.create_message", new=AsyncMock(side_effect=[tool_complete(), tool_complete()])),
            patch("agents.retriever.agent.generate_text", new=AsyncMock(side_effect=["first briefing", "second briefing"])),
            patch("agents.answer.agent.generate_text", new=AsyncMock(side_effect=["first answer [1]", "second answer [1]"])) as answer_model,
        ):
            self._run(
                app.ainvoke(
                    {"question": "What is a PodDisruptionBudget?", "messages": [HumanMessage(content="What is a PodDisruptionBudget?")]},
                    config={"configurable": {"thread_id": "conversation-a"}},
                )
            )
            result = self._run(
                app.ainvoke(
                    {"question": "What is an HPA?", "messages": [HumanMessage(content="What is an HPA?")]},
                    config={"configurable": {"thread_id": "conversation-b"}},
                )
            )

        self.assertIsInstance(answer_model.await_args_list[1].kwargs["prompt"], str)
        self.assertEqual(len(result["messages"]), 2)
        self.assertNotIn("PodDisruptionBudget", str(answer_model.await_args_list[1].kwargs["prompt"]))

    def test_contextual_follow_up_passes_but_obviously_unrelated_question_does_not(self) -> None:
        state = {
            "question": "When would I use one?",
            "messages": [
                HumanMessage(content="Explain PodDisruptionBudgets"),
                AIMessage(content="A PDB protects against voluntary disruptions."),
                HumanMessage(content="When would I use one?"),
            ],
        }
        self.assertTrue(input_guardrail(state)["is_relevant"])

        state["question"] = "What are the football scores?"
        state["messages"][-1] = HumanMessage(content="What are the football scores?")
        self.assertFalse(input_guardrail(state)["is_relevant"])

    def test_recognises_compact_source_follow_up_phrases(self) -> None:
        for question in ("source?", "where's your source?", "which docs?", "where did you get that from?"):
            with self.subTest(question=question):
                self.assertTrue(is_source_only_follow_up(question))

    def test_source_only_follow_up_reuses_thread_scoped_evidence_without_models_or_tools(self) -> None:
        app = build_app(checkpointer=InMemorySaver())
        initial_evidence = {
            "id": "1",
            "type": "kubernetes_docs",
            "title": "Pod Security Standards",
            "source": "pod-security-standards.md",
            "section": "Profiles",
            "url": None,
            "content": "Pod Security Standards define security profiles.",
        }
        with (
            patch("agents.orchestrator.orchestrator.use_tools", new=AsyncMock(return_value={"tool_results": []})) as tools,
            patch(
                "agents.orchestrator.orchestrator.add_context",
                new=AsyncMock(return_value={"context": "Evidence", "sources": [initial_evidence]}),
            ),
            patch("agents.answer.agent.generate_text", new=AsyncMock(return_value="PSS defines profiles. [1]")) as answer_model,
        ):
            # Build after patching: StateGraph stores node callables during construction.
            app = build_app(checkpointer=InMemorySaver())
            config = {"configurable": {"thread_id": "source-thread"}}
            self._run(
                app.ainvoke(
                    {"question": "What are Pod Security Standards?", "messages": [HumanMessage(content="What are Pod Security Standards?")]},
                    config=config,
                )
            )
            result = self._run(
                app.ainvoke(
                    {"question": "wheres ur source", "messages": [HumanMessage(content="wheres ur source")]},
                    config=config,
                )
            )
            other_thread = self._run(
                app.ainvoke(
                    {"question": "source?", "messages": [HumanMessage(content="source?")]},
                    config={"configurable": {"thread_id": "other-thread"}},
                )
            )

        self.assertTrue(result["is_relevant"])
        self.assertEqual(result["answer"], "Sources from the previous answer:")
        self.assertEqual(result["sources"], [initial_evidence])
        self.assertFalse(other_thread["is_relevant"])
        self.assertEqual(other_thread["sources"], [])
        tools.assert_awaited_once()
        answer_model.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
