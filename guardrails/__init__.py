"""Deterministic guardrails for the Kubernetes platform knowledge assistant."""

from .input import RelevanceResult, classify_kubernetes_relevance, is_kubernetes_question

__all__ = ["RelevanceResult", "classify_kubernetes_relevance", "is_kubernetes_question"]
