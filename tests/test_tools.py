"""Focused unit tests for the three agent-facing tools."""

from __future__ import annotations

import json
import base64
import unittest
from email.message import Message
from urllib.error import HTTPError
from unittest.mock import patch

from retrieval.retrieve import RetrievalCandidate
from tools.calculator import calculate
from tools.github import github_kubernetes_lookup
from tools.kubernetes_docs import search_kubernetes_docs
from tools.platform_references import PLATFORM_REFERENCE_SEARCH_TOOL


class FakeResponse:
    def __init__(self, payload: object) -> None:
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.payload


class KubernetesDocsToolTests(unittest.TestCase):
    def test_docs_tool_uses_public_retrieval_api_and_preserves_metadata(self) -> None:
        candidate = RetrievalCandidate(
            chunk_id="statefulset-1",
            text="StatefulSets manage applications that need stable identity.",
            source_document="concepts/workloads/controllers/statefulset.md",
            section="StatefulSets",
            rerank_score=0.97,
        )
        with patch("tools.kubernetes_docs.retrieve", return_value=[candidate]) as retrieve:
            result = search_kubernetes_docs("What is a StatefulSet?", k=3)
        retrieve.assert_called_once_with("What is a StatefulSet?", k=3)
        self.assertEqual(result["chunks"][0]["chunk_id"], "statefulset-1")
        self.assertEqual(result["chunks"][0]["source"], "concepts/workloads/controllers/statefulset.md")
        self.assertEqual(result["chunks"][0]["section"], "StatefulSets")
        self.assertEqual(result["chunks"][0]["reranker_score"], 0.97)


class PlatformReferenceToolTests(unittest.TestCase):
    def test_platform_reference_search_is_domain_restricted_and_capped(self) -> None:
        self.assertEqual(PLATFORM_REFERENCE_SEARCH_TOOL["type"], "web_search_20250305")
        self.assertEqual(PLATFORM_REFERENCE_SEARCH_TOOL["max_uses"], 3)
        self.assertIn("docs.aws.amazon.com", PLATFORM_REFERENCE_SEARCH_TOOL["allowed_domains"])
        self.assertNotIn("*", PLATFORM_REFERENCE_SEARCH_TOOL["allowed_domains"])


class GithubToolTests(unittest.TestCase):
    def test_latest_release(self) -> None:
        payload = {
            "name": "Kubernetes v1.34.0",
            "tag_name": "v1.34.0",
            "published_at": "2026-01-01T00:00:00Z",
            "html_url": "https://github.com/kubernetes/kubernetes/releases/tag/v1.34.0",
            "prerelease": False,
            "body": "Release notes",
        }
        with patch("tools.github.urlopen", return_value=FakeResponse(payload)) as request:
            result = github_kubernetes_lookup("latest_release")
        self.assertTrue(result["ok"])
        self.assertEqual(result["release"]["tag_name"], "v1.34.0")
        self.assertIn("/repos/kubernetes/kubernetes/releases/latest", request.call_args.args[0].full_url)

    def test_changelog_uses_only_a_path_derived_from_the_release_tag(self) -> None:
        content = "# Changelog\n\n## v1.36.0\n\n### Feature\n\n- Adds an example feature.\n\n## v1.35.9\n\n- Older change."
        payload = {
            "encoding": "base64",
            "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
            "html_url": "https://github.com/kubernetes/kubernetes/blob/master/CHANGELOG/CHANGELOG-1.36.md",
        }
        with patch("tools.github.urlopen", return_value=FakeResponse(payload)) as request:
            result = github_kubernetes_lookup("changelog", query="v1.36.0")
        self.assertTrue(result["ok"])
        self.assertEqual(result["version"], "v1.36.0")
        self.assertIn("Adds an example feature", result["content"])
        self.assertNotIn("Older change", result["content"])
        self.assertIn("/contents/CHANGELOG/CHANGELOG-1.36.md", request.call_args.args[0].full_url)

    def test_changelog_rejects_arbitrary_paths(self) -> None:
        result = github_kubernetes_lookup("changelog", query="../../README.md")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "invalid_query")

    def test_issue_search_is_repo_scoped(self) -> None:
        payload = {"items": [{"number": 42, "title": "Fix scheduler", "state": "open", "updated_at": "now", "html_url": "url"}]}
        with patch("tools.github.urlopen", return_value=FakeResponse(payload)) as request:
            result = github_kubernetes_lookup("issues", query="scheduler", limit=2)
        self.assertTrue(result["ok"])
        self.assertEqual(result["issues"][0]["number"], 42)
        url = request.call_args.args[0].full_url
        self.assertIn("/search/issues?", url)
        self.assertIn("repo%3Akubernetes%2Fkubernetes", url)
        self.assertIn("is%3Aissue", url)

    def test_issue_search_treats_query_as_text_not_github_qualifiers(self) -> None:
        with patch("tools.github.urlopen", return_value=FakeResponse({"items": []})) as request:
            github_kubernetes_lookup("issues", query="repo:other/project", limit=2)
        url = request.call_args.args[0].full_url
        self.assertIn("%22repo%3Aother%2Fproject%22", url)
        self.assertIn("repo%3Akubernetes%2Fkubernetes", url)

    def test_rate_limit_returns_structured_failure(self) -> None:
        headers = Message()
        headers["X-RateLimit-Remaining"] = "0"
        error = HTTPError("https://api.github.com", 403, "Forbidden", headers, None)
        with patch("agents.orchestrator.retry.time.sleep"), patch("tools.github.urlopen", side_effect=error) as request:
            result = github_kubernetes_lookup("tags")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "rate_limited")
        self.assertEqual(request.call_count, 5)

    def test_http_failure_returns_structured_failure(self) -> None:
        error = HTTPError("https://api.github.com", 500, "Server Error", Message(), None)
        with patch("agents.orchestrator.retry.time.sleep"), patch("tools.github.urlopen", side_effect=error) as request:
            result = github_kubernetes_lookup("tags")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "http_error")
        self.assertEqual(request.call_count, 5)


class CalculatorToolTests(unittest.TestCase):
    def test_percentage(self) -> None:
        self.assertEqual(calculate("percentage", {"numerator": 3, "denominator": 20})["result"], 15.0)

    def test_error_rate(self) -> None:
        self.assertEqual(calculate("error_rate", {"failures": 96, "requests": 3200})["result"], 3.0)

    def test_resource_utilization(self) -> None:
        self.assertEqual(calculate("resource_utilization", {"used": 850, "limit": 1000})["result"], 85.0)

    def test_division_by_zero(self) -> None:
        with self.assertRaisesRegex(ValueError, "must not be zero"):
            calculate("availability", {"successful": 1, "requests": 0})


if __name__ == "__main__":
    unittest.main()
