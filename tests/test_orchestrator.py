"""Mock-only tests for guarded native Anthropic tool orchestration."""

from __future__ import annotations

import asyncio
import unittest
from typing import Any
from unittest.mock import patch

from agents.orchestrator.orchestrator import build_app, input_guardrail
from agents.answer.agent import INSUFFICIENT_EVIDENCE_MESSAGE


def tool_call(name: str, arguments: dict[str, Any], identifier: str = "toolu_1") -> dict[str, object]:
    return {"content": [{"type": "tool_use", "id": identifier, "name": name, "input": arguments}]}


def tool_complete() -> dict[str, object]:
    return {"content": [{"type": "text", "text": "Evidence collected."}]}


class OrchestratorTests(unittest.TestCase):
    def _run_tool_case(
        self, response: dict[str, object], tool_functions: dict[str, Any]
    ) -> tuple[dict[str, Any], object]:
        with (
            patch("agents.retriever.agent.create_message", side_effect=[response, tool_complete()]) as select_tools,
            patch.dict("agents.retriever.agent.TOOL_FUNCTIONS", tool_functions, clear=False),
            patch("agents.retriever.agent.generate_text", return_value="evidence briefing"),
            patch("agents.answer.agent.generate_text", return_value="grounded answer [1]"),
            patch(
                "agents.answer.agent.create_message",
                return_value={"content": [{"text": '{"allow": true}'}]},
            ),
        ):
            result = asyncio.run(build_app().ainvoke({"question": "Explain this Kubernetes question."}))
        return result, select_tools

    def test_agent_can_select_docs_tool(self) -> None:
        docs = unittest.mock.Mock(
            return_value={
                "query": "StatefulSet",
                "chunks": [
                    {
                        "chunk_id": "statefulset",
                        "text": "StatefulSets manage stateful applications.",
                        "source": "statefulset.md",
                        "section": "StatefulSets",
                        "reranker_score": 0.99,
                    }
                ],
            }
        )
        result, select_tools = self._run_tool_case(
            tool_call("search_kubernetes_docs", {"query": "StatefulSet"}), {"search_kubernetes_docs": docs}
        )
        docs.assert_called_once_with(query="StatefulSet")
        self.assertEqual(result["tool_results"][0]["tool_name"], "search_kubernetes_docs")
        self.assertEqual(result["tool_results"][0]["output"]["chunks"][0]["source"], "statefulset.md")
        self.assertEqual(result["answer"], "grounded answer [1]")
        self.assertEqual(select_tools.call_args.kwargs["tools"][0]["name"], "search_kubernetes_docs")

    def test_agent_can_select_github_tool(self) -> None:
        github = unittest.mock.Mock(
            return_value={
                "resource_type": "latest_release",
                "ok": True,
                "release": {"tag_name": "v1.99.0", "html_url": "https://github.com/kubernetes/kubernetes/releases/tag/v1.99.0"},
            }
        )
        result, _ = self._run_tool_case(
            tool_call("github_kubernetes_lookup", {"resource_type": "latest_release"}),
            {"github_kubernetes_lookup": github},
        )
        github.assert_called_once_with(resource_type="latest_release")
        self.assertEqual(result["tool_results"][0]["output"]["release"]["tag_name"], "v1.99.0")

    def test_agent_can_select_calculator_tool(self) -> None:
        calculator = unittest.mock.Mock(
            return_value={"operation": "resource_utilization", "result": 85.0, "unit": "%"}
        )
        result, _ = self._run_tool_case(
            tool_call("calculate", {"operation": "resource_utilization", "values": {"used": 850, "limit": 1000}}),
            {"calculate": calculator},
        )
        calculator.assert_called_once_with(operation="resource_utilization", values={"used": 850, "limit": 1000})
        self.assertEqual(result["tool_results"][0]["output"]["result"], 85.0)

    def test_agent_can_use_multiple_tools_in_one_round(self) -> None:
        docs = unittest.mock.Mock(return_value={"query": "HPA", "chunks": []})
        calculator = unittest.mock.Mock(
            return_value={"operation": "resource_utilization", "result": 85.0, "unit": "%"}
        )
        response = {
            "content": [
                {"type": "tool_use", "id": "toolu_docs", "name": "search_kubernetes_docs", "input": {"query": "HPA"}},
                {
                    "type": "tool_use",
                    "id": "toolu_math",
                    "name": "calculate",
                    "input": {"operation": "resource_utilization", "values": {"used": 850, "limit": 1000}},
                },
            ]
        }
        result, _ = self._run_tool_case(response, {"search_kubernetes_docs": docs, "calculate": calculator})
        self.assertEqual([item["tool_name"] for item in result["tool_results"]], ["search_kubernetes_docs", "calculate"])
        docs.assert_called_once()
        calculator.assert_called_once()

    def test_agent_preserves_citable_platform_reference_evidence(self) -> None:
        server_search = {
            "content": [
                {"type": "server_tool_use", "id": "srvtoolu_1", "name": "web_search", "input": {"query": "EKS ECS"}},
                {
                    "type": "web_search_tool_result",
                    "tool_use_id": "srvtoolu_1",
                    "content": [{"type": "web_search_result", "url": "https://docs.aws.amazon.com/", "title": "AWS Docs"}],
                },
                {
                    "type": "text",
                    "text": "EKS provides managed Kubernetes, while ECS is AWS-native orchestration.",
                    "citations": [
                        {
                            "url": "https://docs.aws.amazon.com/decision-guides/latest/containers-on-aws-how-to-choose/choosing-aws-container-service.html",
                            "title": "Choosing an AWS container service",
                            "cited_text": "Amazon ECS and Amazon EKS are managed container services.",
                        }
                    ],
                },
            ]
        }
        with (
            patch("agents.retriever.agent.create_message", return_value=server_search) as select_tools,
            patch("agents.retriever.agent.generate_text", return_value="evidence briefing"),
            patch("agents.answer.agent.generate_text", return_value="grounded recommendation [1]"),
            patch(
                "agents.answer.agent.create_message",
                return_value={"content": [{"text": '{"allow": true}'}]},
            ),
        ):
            result = asyncio.run(build_app().ainvoke({"question": "Should I use ECS or EKS?"}))
        output = result["tool_results"][0]["output"]
        self.assertEqual(result["tool_results"][0]["tool_name"], "platform_reference_lookup")
        self.assertTrue(output["ok"])
        self.assertIn("managed Kubernetes", output["summary"])
        self.assertEqual(output["sources"][0]["title"], "Choosing an AWS container service")
        self.assertEqual(select_tools.call_args.kwargs["tools"][-1]["name"], "web_search")

    def test_allowed_question_without_tool_evidence_fails_closed_without_extra_models(self) -> None:
        with (
            patch("agents.retriever.agent.create_message", return_value=tool_complete()),
            patch("agents.retriever.agent.generate_text", return_value="general guidance briefing") as context_model,
            patch("agents.answer.agent.generate_text", return_value="general Kubernetes guidance") as answer_model,
        ):
            result = asyncio.run(build_app().ainvoke({"question": "What PDBs should I set?"}))
        self.assertEqual(result["answer"], INSUFFICIENT_EVIDENCE_MESSAGE)
        context_model.assert_not_called()
        answer_model.assert_not_called()

    def test_irrelevant_questions_stop_before_model_or_tools(self) -> None:
        with (
            patch("agents.orchestrator.orchestrator.create_message") as scope_judge,
            patch("agents.retriever.agent.create_message") as tool_model,
            patch("agents.retriever.agent.generate_text", return_value="general guidance briefing") as context_model,
            patch("agents.answer.agent.generate_text", return_value="grounded answer") as answer_model,
            patch("agents.retriever.agent.TOOL_FUNCTIONS") as tools,
        ):
            result = asyncio.run(build_app().ainvoke({"question": "What is the capital of France?"}))
        self.assertEqual(result["answer"], "I can only answer Kubernetes and related platform infrastructure questions.")
        scope_judge.assert_not_called()
        tool_model.assert_not_called()
        context_model.assert_not_called()
        answer_model.assert_not_called()
        tools.assert_not_called()

    def test_unknown_wording_uses_haiku_and_allows_ambiguous_question(self) -> None:
        with patch(
            "agents.orchestrator.orchestrator.create_message",
            return_value={"content": [{"text": '{"obviously_not_kubernetes_or_infrastructure": false}'}]},
        ) as scope_judge:
            result = asyncio.run(input_guardrail({"question": "explain what this is"}))
        self.assertTrue(result["is_relevant"])
        scope_judge.assert_called_once()

    def test_haiku_rejects_only_an_explicit_obviously_out_of_scope_verdict(self) -> None:
        with patch(
            "agents.orchestrator.orchestrator.create_message",
            return_value={"content": [{"text": '{"obviously_not_kubernetes_or_infrastructure": true}'}]},
        ) as scope_judge:
            result = asyncio.run(input_guardrail({"question": "can you write a limerick?"}))
        self.assertFalse(result["is_relevant"])
        scope_judge.assert_called_once()

    def test_deterministic_kubernetes_question_skips_haiku_scope_judge(self) -> None:
        with patch("agents.orchestrator.orchestrator.create_message") as scope_judge:
            result = asyncio.run(input_guardrail({"question": "Why is my deployment unavailable?"}))
        self.assertTrue(result["is_relevant"])
        scope_judge.assert_not_called()

    def test_unknown_tool_request_is_returned_safely(self) -> None:
        with (
            patch(
                "agents.retriever.agent.create_message",
                side_effect=[tool_call("run_shell", {"command": "uname -a"}), tool_complete()],
            ),
            patch("agents.retriever.agent.generate_text", return_value="general guidance briefing") as context_model,
            patch("agents.answer.agent.generate_text", return_value="grounded answer") as answer_model,
        ):
            result = asyncio.run(build_app().ainvoke({"question": "Kubernetes status"}))
        output = result["tool_results"][0]["output"]
        self.assertFalse(output["ok"])
        self.assertEqual(output["error"]["code"], "unknown_tool")
        self.assertEqual(result["answer"], INSUFFICIENT_EVIDENCE_MESSAGE)
        context_model.assert_not_called()
        answer_model.assert_not_called()


if __name__ == "__main__":
    unittest.main()
