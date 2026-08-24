"""Unit tests for the compact evaluation runner without model calls."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import AsyncMock, patch

from eval.evaluate import _summary, _validated_scores, evaluate_case, judge_answer, run


class EvaluationRunnerTests(unittest.TestCase):
    def test_judge_makes_one_deterministic_low_token_call(self) -> None:
        response = {"content": [{"type": "text", "text": '{"faithfulness":0.9,"relevance":1.0,"correctness":0.8}'}]}
        with patch("eval.evaluate.create_message", new=AsyncMock(return_value=response)) as create:
            scores = asyncio.run(
                judge_answer(
                    question="What is a PodDisruptionBudget?",
                    answer="It constrains voluntary disruptions.",
                    reference=None,
                    context=None,
                )
            )

        self.assertEqual(scores, {"faithfulness": 0.9, "relevance": 1.0, "correctness": 0.8})
        create.assert_awaited_once()
        self.assertEqual(create.call_args.kwargs["temperature"], 0.0)
        self.assertLessEqual(create.call_args.kwargs["max_tokens"], 80)

    def test_evaluate_case_runs_application_and_judge_once(self) -> None:
        case = {"id": "case-1", "category": "kubernetes_docs", "question": "What is a PDB?", "expected": {}}
        state = {"answer": "A PDB constrains voluntary disruption.", "context": "PDB documentation evidence."}
        scores = {"faithfulness": 0.9, "relevance": 1.0, "correctness": 0.8}

        with patch("eval.evaluate.invoke", new=AsyncMock(return_value=state)) as invoke, patch(
            "eval.evaluate.judge_answer", new=AsyncMock(return_value=scores)
        ) as judge:
            result = evaluate_case(case)

        self.assertEqual(result["answer"], state["answer"])
        self.assertEqual({metric: result[metric] for metric in scores}, scores)
        invoke.assert_awaited_once_with(case["question"])
        judge.assert_awaited_once()

    def test_invalid_judge_score_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "out_of_range:faithfulness"):
            _validated_scores('{"faithfulness":1.1,"relevance":1.0,"correctness":1.0}')

    def test_judge_json_wrapped_in_a_single_markdown_fence_is_accepted(self) -> None:
        scores = _validated_scores('```json\n{"faithfulness":0.9,"relevance":1.0,"correctness":0.8}\n```')

        self.assertEqual(scores, {"faithfulness": 0.9, "relevance": 1.0, "correctness": 0.8})

    def test_application_failure_does_not_call_judge(self) -> None:
        case = {"id": "case-2", "question": "What is a PDB?"}
        with patch("eval.evaluate.invoke", new=AsyncMock(side_effect=RuntimeError("failed"))), patch(
            "eval.evaluate.judge_answer", new=AsyncMock()
        ) as judge:
            result = evaluate_case(case)

        self.assertEqual(result["error"], "application_failed:RuntimeError")
        judge.assert_not_awaited()

    def test_summary_excludes_failed_cases_from_averages(self) -> None:
        summary = _summary(
            [
                {"faithfulness": 0.8, "relevance": 0.9, "correctness": 1.0},
                {"error": "judge_response_invalid"},
            ]
        )

        self.assertEqual(summary["count"], 1)
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(summary["avg_faithfulness"], 0.8)

    def test_run_applies_offset_before_limit(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "golden.jsonl"
            path.write_text("\n".join(json.dumps({"question": f"case-{index}"}) for index in range(3)), encoding="utf-8")
            results_path = Path(directory) / "results.jsonl"
            summary_path = Path(directory) / "summary.json"
            scored_result = {"faithfulness": 1.0, "relevance": 1.0, "correctness": 1.0}
            with patch("eval.evaluate.GOLDEN_SET_PATH", path), patch("eval.evaluate.RESULTS_PATH", results_path), patch(
                "eval.evaluate.SUMMARY_PATH", summary_path
            ), patch("eval.evaluate.evaluate_case", return_value=scored_result) as evaluate, patch(
                "eval.evaluate.flush_traces"
            ):
                summary = run(offset=1, limit=1)

        self.assertEqual(summary["count"], 1)
        evaluate.assert_called_once_with({"question": "case-1"})


if __name__ == "__main__":
    unittest.main()
