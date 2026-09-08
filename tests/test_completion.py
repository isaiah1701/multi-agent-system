"""Tests for complete responses at hard model token limits."""

import unittest

from agents.orchestrator.shared.completion import trim_incomplete_final_sentence


class CompletionTests(unittest.TestCase):
    def test_removes_only_the_unfinished_cutoff_fragment(self) -> None:
        answer = "Check pod events first. Then inspect the container logs and verify"
        self.assertEqual(trim_incomplete_final_sentence(answer), "Check pod events first.")

    def test_preserves_a_complete_cited_answer(self) -> None:
        answer = "A PodDisruptionBudget limits voluntary disruptions. [1]"
        self.assertEqual(trim_incomplete_final_sentence(answer), answer)

    def test_does_not_treat_a_cutoff_abbreviation_as_a_finished_sentence(self) -> None:
        answer = "Use EKS for Kubernetes. Cut traffic over gradually (e."
        self.assertEqual(trim_incomplete_final_sentence(answer), "Use EKS for Kubernetes.")


if __name__ == "__main__":
    unittest.main()
