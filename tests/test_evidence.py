"""Regression coverage for durable evidence normalization."""

from __future__ import annotations

import unittest

from agents.orchestrator.shared.evidence import MAX_DOCUMENT_EVIDENCE, build_evidence, format_evidence_for_prompt


class EvidenceTests(unittest.TestCase):
    def test_builds_public_provenance_for_each_supported_tool_type(self) -> None:
        evidence = build_evidence(
            [
                {
                    "tool_name": "search_kubernetes_docs",
                    "output": {
                        "chunks": [
                            {
                                "chunk_id": str(index),
                                "text": f"Document chunk {index}",
                                "source": "pod-security-standards.md",
                                "section": "Pod Security Standards > Restricted",
                            }
                            for index in range(5)
                        ]
                    },
                },
                {
                    "tool_name": "github_kubernetes_lookup",
                    "output": {
                        "release": {
                            "tag_name": "v1.34.0",
                            "html_url": "https://github.com/kubernetes/kubernetes/releases/tag/v1.34.0",
                        }
                    },
                },
                {
                    "tool_name": "platform_reference_lookup",
                    "output": {
                        "sources": [
                            {
                                "title": "Amazon EKS documentation",
                                "url": "https://docs.aws.amazon.com/eks/latest/userguide/",
                                "excerpt": "EKS is managed Kubernetes.",
                            }
                        ]
                    },
                },
                {
                    "tool_name": "calculate",
                    "input": {"values": {"used": 850, "limit": 1000}},
                    "output": {"operation": "resource_utilization", "result": 85.0, "unit": "%"},
                },
            ]
        )

        self.assertEqual(len([item for item in evidence if item["type"] == "kubernetes_docs"]), MAX_DOCUMENT_EVIDENCE)
        self.assertEqual([item["id"] for item in evidence], [str(index) for index in range(1, len(evidence) + 1)])
        self.assertTrue(any(item["type"] == "github" and item["url"] for item in evidence))
        self.assertTrue(any(item["type"] == "platform_reference" and item["url"] for item in evidence))
        calculation = next(item for item in evidence if item["type"] == "calculation")
        self.assertIsNone(calculation["url"])
        self.assertIn("85%", calculation["title"])
        self.assertIn("850 / 1000 × 100", calculation["title"])
        self.assertIn("[1]", format_evidence_for_prompt(evidence))


if __name__ == "__main__":
    unittest.main()
