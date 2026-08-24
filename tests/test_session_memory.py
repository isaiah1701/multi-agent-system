"""Credit-free checks for LangGraph in-memory conversational sessions."""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from agents.orchestrator.orchestrator import build_app, input_guardrail


def tool_complete() -> dict[str, object]:
    return {"content": [{"type": "text", "text": "Evidence collected."}]}


class SessionMemoryTests(unittest.TestCase):
    def _run(self, coroutine: object) -> object:
        return asyncio.run(coroutine)  # type: ignore[arg-type]

    def test_same_thread_keeps_prior_turn_and_separates_current_question(self) -> None:
        app = build_app(checkpointer=InMemorySaver())
        with (
            patch("agents.sub_agents.retrieve.create_message", new=AsyncMock(side_effect=[tool_complete(), tool_complete()])),
            patch("agents.sub_agents.retrieve.generate_text", new=AsyncMock(side_effect=["first briefing", "second briefing"])),
            patch("agents.sub_agents.answer.generate_text", new=AsyncMock(side_effect=["first answer", "second answer"])) as answer_model,
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
        self.assertIn("first answer", second_prompt[0]["text"])
        self.assertIn("When should I use one?", second_prompt[1]["text"])
        self.assertEqual(len(result["messages"]), 4)

    def test_different_threads_are_isolated(self) -> None:
        app = build_app(checkpointer=InMemorySaver())
        with (
            patch("agents.sub_agents.retrieve.create_message", new=AsyncMock(side_effect=[tool_complete(), tool_complete()])),
            patch("agents.sub_agents.retrieve.generate_text", new=AsyncMock(side_effect=["first briefing", "second briefing"])),
            patch("agents.sub_agents.answer.generate_text", new=AsyncMock(side_effect=["first answer", "second answer"])) as answer_model,
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


if __name__ == "__main__":
    unittest.main()
