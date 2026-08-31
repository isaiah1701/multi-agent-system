"""Tests for the deterministic primary output gate and narrow Haiku fallback."""

from __future__ import annotations

import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

from agents.answer.agent import (
    ANSWER_DOCUMENT_CHUNK_LIMIT,
    BLOCKED_OUTPUT_MESSAGE,
    INSUFFICIENT_EVIDENCE_MESSAGE,
    UNVERIFIED_OUTPUT_MESSAGE,
    answer,
    build_answer_system_prompt,
    format_answer_tool_results,
    select_answer_budget,
)
from guardrails import inspect_output, parse_backup_judge_allow
from agents.orchestrator.llm.config import ANSWER_EXTENDED_MAX_TOKENS, ANSWER_MAX_TOKENS, ANSWER_TARGET_WORDS


class OutputGuardrailTests(unittest.TestCase):
    source = {
        "id": "1",
        "type": "kubernetes_docs",
        "title": "Pod Disruptions",
        "source": "pod-disruptions.md",
        "section": "PodDisruptionBudget",
        "url": None,
        "content": "A PDB limits voluntary disruptions.",
    }

    def test_sensitive_output_is_blocked(self) -> None:
        result = inspect_output("Use ANTHROPIC_API_KEY=sk-ant-abcdefghijklmnopqrstuvwxyz", [])

        self.assertEqual(result.decision, "block")
        self.assertIn("anthropic_api_key", result.reasons)

    def test_stack_trace_is_blocked(self) -> None:
        result = inspect_output('Traceback (most recent call last):\n  File "app.py", line 12', [])

        self.assertEqual(result.decision, "block")
        self.assertIn("internal_stack_trace", result.reasons)

    def test_external_evidence_without_attribution_requires_review(self) -> None:
        tool_results = [{"tool_name": "search_kubernetes_docs", "output": {"chunks": [{"text": "PDB docs"}]}}]

        self.assertEqual(inspect_output("A PDB limits voluntary disruption.", tool_results).decision, "review")
        self.assertEqual(
            inspect_output("According to Kubernetes docs, a PDB limits voluntary disruption.", tool_results).decision,
            "allow",
        )

    def test_calculation_evidence_does_not_require_attribution(self) -> None:
        tool_results = [{"tool_name": "calculate", "output": {"result": 85.0}}]

        self.assertEqual(inspect_output("Your resource utilization is 85%.", tool_results).decision, "allow")

    def test_structured_evidence_requires_valid_citation_ids(self) -> None:
        self.assertEqual(
            inspect_output("A PDB limits voluntary disruption. [1]", [], [self.source]).decision, "allow"
        )
        self.assertEqual(
            inspect_output("A PDB limits voluntary disruption.", [], [self.source]).decision, "block"
        )
        self.assertEqual(
            inspect_output("A PDB limits voluntary disruption. [9]", [], [self.source]).decision, "block"
        )

    def test_backup_judge_parser_accepts_only_boolean_json(self) -> None:
        self.assertTrue(parse_backup_judge_allow({"content": [{"text": '```json\n{"allow": true}\n```'}]}))
        self.assertIsNone(parse_backup_judge_allow({"content": [{"text": "allow it"}]}))

    def test_answer_prompt_reserves_space_for_a_complete_conversational_reply(self) -> None:
        state = {"question": "What is a PDB?", "context": "Evidence", "tool_results": [], "sources": [self.source]}
        with patch(
            "agents.answer.agent.generate_text",
            new=AsyncMock(return_value="A PDB limits voluntary disruption. [1]"),
        ) as generate:
            asyncio.run(answer(state))

        self.assertEqual(generate.await_args.kwargs["max_tokens"], ANSWER_MAX_TOKENS)
        system = generate.await_args.kwargs["system"]
        self.assertIn(f"about {ANSWER_TARGET_WORDS} words", system)
        self.assertIn("Finish every sentence", system)
        self.assertIn("Do not use Markdown headings", system)
        self.assertIn("Structured evidence", str(generate.await_args.kwargs["prompt"]))

    def test_answer_removes_an_insufficient_evidence_preamble_from_a_cited_answer(self) -> None:
        mixed_answer = (
            f"{INSUFFICIENT_EVIDENCE_MESSAGE} "
            "A PDB limits voluntary disruption during maintenance. [1]"
        )
        state = {"question": "What is a PDB?", "context": "Evidence", "tool_results": [], "sources": [self.source]}
        with patch("agents.answer.agent.generate_text", new=AsyncMock(return_value=mixed_answer)):
            result = asyncio.run(answer(state))

        self.assertEqual(result["answer"], "A PDB limits voluntary disruption during maintenance. [1]")

    def test_answer_removes_an_insufficient_evidence_suffix_from_a_cited_answer(self) -> None:
        mixed_answer = (
            "A PDB limits voluntary disruption during maintenance. [1] "
            f"{INSUFFICIENT_EVIDENCE_MESSAGE}"
        )
        state = {"question": "What is a PDB?", "context": "Evidence", "tool_results": [], "sources": [self.source]}
        with patch("agents.answer.agent.generate_text", new=AsyncMock(return_value=mixed_answer)):
            result = asyncio.run(answer(state))

        self.assertEqual(result["answer"], "A PDB limits voluntary disruption during maintenance. [1]")

    def test_answer_removes_a_broader_insufficient_evidence_sentence_from_a_cited_answer(self) -> None:
        mixed_answer = (
            "ECS is simpler to operate. [1] "
            "The available evidence does not compare EKS, so I don't have enough sourced evidence to recommend one."
        )
        state = {"question": "ECS or EKS?", "context": "Evidence", "tool_results": [], "sources": [self.source]}
        with patch("agents.answer.agent.generate_text", new=AsyncMock(return_value=mixed_answer)):
            result = asyncio.run(answer(state))

        self.assertEqual(result["answer"], "ECS is simpler to operate. [1]")

    def test_budget_guardrail_keeps_ordinary_questions_on_the_small_cap(self) -> None:
        budget = select_answer_budget("What is a PodDisruptionBudget?")

        self.assertFalse(budget.is_extended)
        self.assertEqual(budget.max_tokens, ANSWER_MAX_TOKENS)

    def test_budget_guardrail_allows_extended_cap_for_an_urgent_detailed_request(self) -> None:
        budget = select_answer_budget("Create a detailed runbook for an urgent production outage.")

        self.assertTrue(budget.is_extended)
        self.assertEqual(budget.max_tokens, ANSWER_EXTENDED_MAX_TOKENS)
        self.assertIn("explicitly requested detail", build_answer_system_prompt(budget))

    def test_answer_evidence_contains_only_three_documentation_chunks(self) -> None:
        first_chunks = [{"chunk_id": f"first-{index}"} for index in range(5)]
        second_chunks = [{"chunk_id": f"second-{index}"} for index in range(5)]
        tool_results = [
            {"tool_name": "search_kubernetes_docs", "output": {"chunks": first_chunks}},
            {"tool_name": "search_kubernetes_docs", "output": {"chunks": second_chunks}},
            {"tool_name": "calculate", "output": {"result": 85}},
        ]

        compacted = json.loads(format_answer_tool_results(tool_results))

        self.assertEqual(len(compacted[0]["output"]["chunks"]), ANSWER_DOCUMENT_CHUNK_LIMIT)
        self.assertEqual(compacted[1]["output"]["chunks"], [])
        self.assertEqual(compacted[2]["output"]["result"], 85)
        self.assertEqual(len(first_chunks), 5)
        self.assertEqual(len(second_chunks), 5)

    def test_guarded_stream_releases_only_checked_fragments_then_flushes_the_answer(self) -> None:
        draft = ("A PDB limits voluntary disruptions while keeping maintenance safer. [1] " * 5).strip()
        streamed: list[str] = []
        resets: list[str] = []

        async def generate_with_fragments(**kwargs: object) -> str:
            on_text = kwargs["on_text"]
            for start in range(0, len(draft), 40):
                await on_text(draft[start : start + 40])  # type: ignore[misc]
            return draft

        state = {"question": "What is a PDB?", "context": "Evidence", "tool_results": [], "sources": [self.source]}
        with patch("agents.answer.agent.generate_text", side_effect=generate_with_fragments):
            result = asyncio.run(
                answer(
                    state,
                    {
                        "configurable": {
                            "answer_stream_handler": streamed.append,
                            "answer_stream_reset_handler": resets.append,
                        }
                    },
                )
            )

        self.assertEqual(result["answer"], draft)
        self.assertEqual("".join(streamed), draft)
        self.assertEqual(resets, [])

    def test_guarded_stream_replaces_a_blocked_draft_with_the_safe_fallback(self) -> None:
        draft = "ANTHROPIC_API_KEY=sk-ant-abcdefghijklmnopqrstuvwxyz"
        streamed: list[str] = []
        resets: list[str] = []

        async def generate_blocked_fragment(**kwargs: object) -> str:
            await kwargs["on_text"](draft)  # type: ignore[misc]
            return draft

        state = {"question": "What is a PDB?", "context": "Evidence", "tool_results": [], "sources": [self.source]}
        with patch("agents.answer.agent.generate_text", side_effect=generate_blocked_fragment):
            result = asyncio.run(
                answer(
                    state,
                    {
                        "configurable": {
                            "answer_stream_handler": streamed.append,
                            "answer_stream_reset_handler": resets.append,
                        }
                    },
                )
            )

        self.assertEqual(result["answer"], BLOCKED_OUTPUT_MESSAGE)
        self.assertEqual(streamed, [])
        self.assertEqual(resets, [BLOCKED_OUTPUT_MESSAGE])

    def test_hard_block_does_not_call_haiku_or_stream_draft(self) -> None:
        state = {"question": "What is a PDB?", "context": "Evidence", "tool_results": [], "sources": [self.source]}
        streamed: list[str] = []
        with patch("agents.answer.agent.generate_text", new=AsyncMock(return_value="Traceback (most recent call last):")), patch(
            "agents.answer.agent.create_message", new=AsyncMock()
        ) as judge:
            result = asyncio.run(answer(state, {"configurable": {"answer_stream_handler": streamed.append}}))

        self.assertEqual(result["answer"], BLOCKED_OUTPUT_MESSAGE)
        self.assertEqual(streamed, [BLOCKED_OUTPUT_MESSAGE])
        judge.assert_not_awaited()

    def test_review_uses_haiku_backup_and_safe_fallback_on_rejection(self) -> None:
        state = {"question": "What is a PDB?", "context": "Evidence", "tool_results": [], "sources": [self.source]}
        with patch("agents.answer.agent.generate_text", new=AsyncMock(return_value="A PDB constrains disruption.")), patch(
            "agents.answer.agent.create_message",
            new=AsyncMock(return_value={"content": [{"text": '{"allow": false}'}]}),
        ) as judge:
            result = asyncio.run(answer(state))

        self.assertEqual(result["answer"], INSUFFICIENT_EVIDENCE_MESSAGE)
        judge.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
