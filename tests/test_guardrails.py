"""Regression coverage for local Kubernetes-platform relevance classification."""

from __future__ import annotations

import unittest

from guardrails import classify_kubernetes_relevance, is_kubernetes_question


class RelevanceGuardrailTests(unittest.TestCase):
    def test_kubernetes_shorthand_and_slash_language_are_allowed(self) -> None:
        questions = (
            "What PDBs should I set?",
            "why is my PVC pending?",
            "hpa isn't scaling",
            "my pod got oomkilled",
            "cni problem",
            "crashloop again",
            "my pods are cooked why",
            "pdbs keep blocking my drain",
            "why's this thing crashlooping",
            "my svc ain't reachable",
            "service vs headless service",
            "why's ingress throwing 502s",
            "pods keep getting nuked when node drains",
        )
        for question in questions:
            with self.subTest(question=question):
                self.assertTrue(is_kubernetes_question(question))

    def test_ecosystem_and_cloud_platform_questions_are_allowed(self) -> None:
        questions = (
            "how do I deploy ArgoCD?",
            "should Backstage run on Kubernetes?",
            "helm vs plain manifests",
            "karpenter vs cluster autoscaler",
            "how do I install prometheus?",
            "how should I configure EKS node groups?",
            "what AWS load balancer should I use for ingress?",
            "what instance type should run my worker nodes?",
            "how does IAM work with EKS?",
            "AKS vs EKS?",
            "how do I expose this service through AWS?",
            "what would this architecture look like on Azure?",
            "how would terraform provision this cluster?",
        )
        for question in questions:
            with self.subTest(question=question):
                self.assertTrue(is_kubernetes_question(question))

    def test_ai_platform_and_workload_questions_are_allowed(self) -> None:
        questions = (
            "how do I run GPUs on Kubernetes?",
            "what is NVIDIA GPU Operator?",
            "can I deploy vLLM on K8s?",
            "should Langfuse run inside the cluster?",
            "can my vector database run on Kubernetes?",
            "how do I deploy postgres here?",
            "what resource requests should I give this app?",
            "is gp3 good for persistent volumes?",
        )
        for question in questions:
            with self.subTest(question=question):
                self.assertTrue(is_kubernetes_question(question))

    def test_short_ambiguous_platform_follow_ups_are_allowed(self) -> None:
        for question in ("what about storage?", "what instance type then?", "should I use Helm?"):
            with self.subTest(question=question):
                result = classify_kubernetes_relevance(question)
                self.assertTrue(result.allowed)
                self.assertGreaterEqual(result.confidence, 0.55)

    def test_clearly_unrelated_questions_are_rejected(self) -> None:
        questions = (
            "recipe for lasagna",
            "football scores",
            "write a poem",
            "history of Rome",
            "gym workout",
            "How do I install Docker Desktop on Windows?",
            "What is the capital of Mongolia?",
        )
        for question in questions:
            with self.subTest(question=question):
                self.assertFalse(is_kubernetes_question(question))

    def test_result_retains_internal_domain_categories(self) -> None:
        result = classify_kubernetes_relevance("How do I deploy ArgoCD on EKS?")
        self.assertTrue(result.allowed)
        self.assertIn("kubernetes_ecosystem", result.matched_domains)
        self.assertIn("managed_kubernetes", result.matched_domains)

    def test_corpus_headings_supply_a_fallback_domain_vocabulary(self) -> None:
        result = classify_kubernetes_relevance("Can you explain topology-aware routing?")
        self.assertTrue(result.allowed)
        self.assertIn("kubernetes_docs", result.matched_domains)

    def test_corpus_phrase_matching_handles_singular_plural_variants(self) -> None:
        result = classify_kubernetes_relevance("service vs headless service")
        self.assertTrue(result.allowed)
        self.assertIn("kubernetes_docs", result.matched_domains)
        self.assertFalse(is_kubernetes_question("delivery service pricing"))


if __name__ == "__main__":
    unittest.main()
