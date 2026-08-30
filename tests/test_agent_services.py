"""Contract tests for independently runnable retrieval and answer services."""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from agents.answer.service import app as answer_app
from agents.retriever.service import app as retriever_app


class AgentServiceTests(unittest.TestCase):
    def _request(self, app: object, method: str, path: str, **kwargs: object) -> httpx.Response:
        async def request() -> httpx.Response:
            transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
            async with httpx.AsyncClient(transport=transport, base_url="http://internal") as client:
                return await client.request(method, path, **kwargs)

        return asyncio.run(request())

    def test_each_private_agent_has_a_dependency_free_health_endpoint(self) -> None:
        for app in (retriever_app, answer_app):
            with self.subTest(app=app.title):
                response = self._request(app, "GET", "/health")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), {"status": "ok"})

    def test_retriever_contract_passes_only_question_and_history_to_the_agent(self) -> None:
        evidence = {"id": "1", "type": "kubernetes_docs", "title": "Pods", "source": "pods.md"}
        with (
            patch(
                "agents.retriever.service.use_tools",
                new=AsyncMock(return_value={"tool_results": [{"tool_name": "search_kubernetes_docs"}]}),
            ) as use_tools,
            patch(
                "agents.retriever.service.add_context",
                new=AsyncMock(return_value={"context": "Pod evidence", "sources": [evidence]}),
            ) as add_context,
        ):
            response = self._request(
                retriever_app,
                "POST",
                "/v1/retrieve",
                json={"question": "Explain Pods", "history": [{"role": "assistant", "content": "Earlier"}]},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["context"], "Pod evidence")
        self.assertEqual(use_tools.await_args.args[0]["messages"], [{"role": "assistant", "content": "Earlier"}])
        self.assertEqual(add_context.await_args.args[0]["tool_results"][0]["tool_name"], "search_kubernetes_docs")

    def test_answer_contract_returns_only_the_guarded_answer_and_sources(self) -> None:
        evidence = {"id": "1", "type": "kubernetes_docs", "title": "Pods", "source": "pods.md"}
        with patch(
            "agents.answer.service.answer",
            new=AsyncMock(return_value={"answer": "Pods run containers. [1]", "sources": [evidence]}),
        ) as answer:
            response = self._request(
                answer_app,
                "POST",
                "/v1/answer",
                json={
                    "question": "What are Pods?",
                    "history": [],
                    "tool_results": [{"tool_name": "search_kubernetes_docs"}],
                    "context": "Pod evidence",
                    "sources": [evidence],
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"answer": "Pods run containers. [1]", "sources": [evidence]})
        self.assertEqual(answer.await_args.args[0]["context"], "Pod evidence")


if __name__ == "__main__":
    unittest.main()
