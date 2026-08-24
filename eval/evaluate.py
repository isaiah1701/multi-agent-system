"""Ultra-low-cost answer evaluation for the Kubernetes assistant."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import time
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from agents.orchestrator.orchestrator import invoke
from llm.client import create_message
from llm.config import JUDGE_MAX_TOKENS, JUDGE_MODEL
from observability import flush_traces, observe, update_current_span


EVAL_DIRECTORY = Path(__file__).resolve().parent
GOLDEN_SET_PATH = EVAL_DIRECTORY / "golden_set.jsonl"
RESULTS_PATH = EVAL_DIRECTORY / "results.jsonl"
SUMMARY_PATH = EVAL_DIRECTORY / "summary.json"
METRICS = ("faithfulness", "relevance", "correctness")
MAX_JUDGE_CONTEXT_CHARS = 1_200
JUDGE_SYSTEM_PROMPT = (
    "Score a Kubernetes assistant. Return only JSON with numeric faithfulness, relevance, and correctness from 0.0 to 1.0. "
    "Faithfulness means no unsupported claims versus EVIDENCE; relevance means directly answering QUESTION; "
    "correctness means matching REFERENCE or established Kubernetes facts. Do not use Markdown code fences."
)


def _field(value: Any, name: str, default: Any = None) -> Any:
    return value.get(name, default) if isinstance(value, Mapping) else getattr(value, name, default)


def _short_text(value: object, limit: int = 300) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= limit else f"{text[:limit].rstrip()}…"


class JudgeResponseError(ValueError):
    """A validated failure that safely retains a short judge-output preview."""

    def __init__(self, code: str, response_text: str) -> None:
        super().__init__(code)
        self.code = code
        self.response_preview = _short_text(response_text)


def _case_error(
    case: Mapping[str, Any] | None,
    error: str,
    *,
    stage: str,
    line: int | None = None,
    details: Mapping[str, Any] | None = None,
    timing_ms: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {"status": "failed", "failure_stage": stage, "error": error}
    if case is not None:
        for name in ("id", "category", "question"):
            if name in case:
                result[name] = case[name]
    if line is not None:
        result["line"] = line
    if details:
        result["error_details"] = dict(details)
    if timing_ms:
        result["timing_ms"] = dict(timing_ms)
    return result


def load_cases(path: Path) -> Iterable[Mapping[str, Any] | dict[str, Any]]:
    """Yield parsed cases while retaining malformed rows as result records."""
    with path.open(encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            if not raw_line.strip():
                continue
            try:
                case = json.loads(raw_line)
            except json.JSONDecodeError:
                yield {"_load_error": "malformed_jsonl", "_line": line_number}
                continue
            if not isinstance(case, Mapping):
                yield {"_load_error": "golden_case_not_object", "_line": line_number}
                continue
            yield case


def _reference(case: Mapping[str, Any]) -> str | None:
    """Keep only answer-relevant expectations; tool-routing checks are not judge input."""
    expected = case.get("expected")
    if not isinstance(expected, Mapping):
        return None
    reference = {
        name: expected[name]
        for name in ("is_relevant", "answer_requirements", "calculation")
        if name in expected
    }
    return json.dumps(reference, ensure_ascii=False, separators=(",", ":")) if reference else None


def _response_text(response: Any) -> str:
    """Extract text from the native Anthropic response without provider-specific imports."""
    content = _field(response, "content", [])
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    return "".join(
        text
        for block in content
        if _field(block, "type") == "text"
        and isinstance((text := _field(block, "text")), str)
    ).strip()


def _validated_scores(response_text: str) -> dict[str, float]:
    response_text = response_text.strip()
    if response_text.startswith("```") and response_text.endswith("```"):
        fenced_lines = response_text.splitlines()
        if len(fenced_lines) >= 3:
            response_text = "\n".join(fenced_lines[1:-1]).strip()
    try:
        payload = json.loads(response_text)
    except json.JSONDecodeError:
        # Haiku occasionally adds a short preface despite the JSON-only prompt.
        # Accept one embedded JSON object, while still validating every score below.
        start = response_text.find("{")
        if start < 0:
            raise JudgeResponseError("judge_response_invalid", response_text)
        try:
            payload, _ = json.JSONDecoder().raw_decode(response_text[start:])
        except json.JSONDecodeError as error:
            raise JudgeResponseError("judge_response_invalid", response_text) from error
    if not isinstance(payload, Mapping):
        raise JudgeResponseError("judge_response_invalid", response_text)

    scores: dict[str, float] = {}
    for metric in METRICS:
        value = payload.get(metric)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise JudgeResponseError(f"judge_metric_missing_or_invalid:{metric}", response_text)
        score = float(value)
        if not math.isfinite(score) or not 0.0 <= score <= 1.0:
            raise JudgeResponseError(f"judge_metric_out_of_range:{metric}", response_text)
        scores[metric] = score
    return scores


async def judge_answer(
    *, question: str, answer: str, reference: str | None, context: str | None
) -> dict[str, float]:
    """Use exactly one compact Haiku call to score all answer-level metrics."""
    prompt_parts = [f"QUESTION:\n{question}"]
    if reference:
        prompt_parts.append(f"REFERENCE:\n{reference}")
    if context:
        prompt_parts.append(f"EVIDENCE:\n{context[:MAX_JUDGE_CONTEXT_CHARS]}")
    prompt_parts.extend(
        (
            f"ANSWER:\n{answer}",
            'Return only {"faithfulness":0.0,"relevance":0.0,"correctness":0.0}.',
        )
    )
    response = await create_message(
        model=JUDGE_MODEL,
        system=JUDGE_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": "\n\n".join(prompt_parts)}],
        max_tokens=JUDGE_MAX_TOKENS,
        temperature=0.0,
    )
    return _validated_scores(_response_text(response))


def _tool_summary(state: Mapping[str, Any]) -> dict[str, Any]:
    tool_results = state.get("tool_results")
    if not isinstance(tool_results, list):
        return {"tool_count": 0, "tool_names": [], "tool_failures": 0}
    names: list[str] = []
    failures = 0
    for result in tool_results:
        if not isinstance(result, Mapping):
            continue
        name = result.get("tool_name")
        if isinstance(name, str):
            names.append(name)
        output = result.get("output")
        if isinstance(output, Mapping) and output.get("ok") is False:
            failures += 1
    return {"tool_count": len(names), "tool_names": names, "tool_failures": failures}


def _timing(started_at: float, *, application_ms: int | None = None, judge_ms: int | None = None) -> dict[str, int]:
    timing = {"total": round((time.perf_counter() - started_at) * 1000)}
    if application_ms is not None:
        timing["application"] = application_ms
    if judge_ms is not None:
        timing["judge"] = judge_ms
    return timing


@observe(name="golden-set-evaluation-case", as_type="chain", capture_input=False, capture_output=False)
def evaluate_case(case: Mapping[str, Any]) -> dict[str, Any]:
    """Run the production path once, then judge its usable final answer once."""
    started_at = time.perf_counter()
    load_error = case.get("_load_error")
    if isinstance(load_error, str):
        result = _case_error(None, load_error, stage="input", line=case.get("_line"), timing_ms=_timing(started_at))
        update_current_span(output=result, level="ERROR", status_message=load_error)
        return result

    question = case.get("question")
    if not isinstance(question, str) or not question.strip():
        result = _case_error(case, "missing_question", stage="input", timing_ms=_timing(started_at))
        update_current_span(output=result, level="ERROR", status_message="missing_question")
        return result
    update_current_span(input={"id": case.get("id"), "category": case.get("category"), "question": question})

    application_started_at = time.perf_counter()
    try:
        state = asyncio.run(invoke(question))
    except Exception as error:
        result = _case_error(
            case,
            f"application_failed:{type(error).__name__}",
            stage="application",
            details={"message": _short_text(error)},
            timing_ms=_timing(started_at, application_ms=round((time.perf_counter() - application_started_at) * 1000)),
        )
        update_current_span(output=result, level="ERROR", status_message=result["error"])
        return result
    application_ms = round((time.perf_counter() - application_started_at) * 1000)

    answer = state.get("answer") if isinstance(state, Mapping) else None
    if not isinstance(answer, str) or not answer.strip():
        result = _case_error(
            case,
            "empty_answer",
            stage="application",
            details=_tool_summary(state) if isinstance(state, Mapping) else None,
            timing_ms=_timing(started_at, application_ms=application_ms),
        )
        update_current_span(output=result, level="ERROR", status_message="empty_answer")
        return result

    context = state.get("context") if isinstance(state, Mapping) else None
    judge_started_at = time.perf_counter()
    try:
        scores = asyncio.run(
            judge_answer(
                question=question,
                answer=answer.strip(),
                reference=_reference(case),
                context=context if isinstance(context, str) and context.strip() else None,
            )
        )
    except JudgeResponseError as error:
        result = _case_error(
            case,
            error.code,
            stage="judge",
            details={"judge_response_preview": error.response_preview},
            timing_ms=_timing(
                started_at,
                application_ms=application_ms,
                judge_ms=round((time.perf_counter() - judge_started_at) * 1000),
            ),
        )
        update_current_span(output=result, level="ERROR", status_message=error.code)
        return result
    except ValueError as error:
        result = _case_error(
            case,
            str(error),
            stage="judge",
            timing_ms=_timing(
                started_at,
                application_ms=application_ms,
                judge_ms=round((time.perf_counter() - judge_started_at) * 1000),
            ),
        )
        update_current_span(output=result, level="ERROR", status_message=str(error))
        return result
    except Exception as error:
        result = _case_error(
            case,
            f"judge_failed:{type(error).__name__}",
            stage="judge",
            details={"message": _short_text(error)},
            timing_ms=_timing(
                started_at,
                application_ms=application_ms,
                judge_ms=round((time.perf_counter() - judge_started_at) * 1000),
            ),
        )
        update_current_span(output=result, level="ERROR", status_message=result["error"])
        return result

    result = {
        "status": "scored",
        "id": case.get("id"),
        "category": case.get("category"),
        "question": question,
        "answer": answer.strip(),
        "is_relevant": state.get("is_relevant") if isinstance(state, Mapping) else None,
        "tool_summary": _tool_summary(state) if isinstance(state, Mapping) else None,
        "timing_ms": _timing(
            started_at,
            application_ms=application_ms,
            judge_ms=round((time.perf_counter() - judge_started_at) * 1000),
        ),
        **scores,
    }
    update_current_span(output={"status": "scored", "scores": scores, "timing_ms": result["timing_ms"]})
    return result


def _summary(results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    scored = [result for result in results if all(metric in result for metric in METRICS)]
    failed = [result for result in results if "error" in result]
    return {
        "attempted": len(results),
        "count": len(scored),
        "failed": len(failed),
        "failure_reasons": dict(sorted(Counter(str(result["error"]) for result in failed).items())),
        "failure_stages": dict(sorted(Counter(str(result.get("failure_stage", "unknown")) for result in failed).items())),
        **{
            f"avg_{metric}": round(sum(float(result[metric]) for result in scored) / len(scored), 4)
            if scored
            else None
            for metric in METRICS
        },
    }


def write_jsonl(path: Path, records: Iterable[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")


def _write_checkpoint(results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    summary = _summary(results)
    write_jsonl(RESULTS_PATH, results)
    SUMMARY_PATH.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary


def _print_case_progress(index: int, total: int, result: Mapping[str, Any]) -> None:
    case_id = result.get("id", f"line-{result.get('line', '?')}")
    timing = result.get("timing_ms")
    elapsed = timing.get("total") if isinstance(timing, Mapping) else None
    suffix = f" in {elapsed}ms" if isinstance(elapsed, int) else ""
    if "error" in result:
        print(f"[{index}/{total}] {case_id}: failed at {result.get('failure_stage')} ({result['error']}){suffix}", flush=True)
    else:
        print(f"[{index}/{total}] {case_id}: scored{suffix}", flush=True)


def run(*, limit: int | None = None, offset: int = 0) -> dict[str, Any]:
    """Evaluate a contiguous slice of the golden set and persist compact results."""
    if limit is not None and limit < 1:
        raise ValueError("limit must be at least 1")
    if offset < 0:
        raise ValueError("offset must not be negative")
    cases = list(load_cases(GOLDEN_SET_PATH))
    cases = cases[offset:]
    if limit is not None:
        cases = cases[:limit]
    results: list[dict[str, Any]] = []
    _write_checkpoint(results)
    try:
        for index, case in enumerate(cases, start=1):
            results.append(evaluate_case(case))
            _write_checkpoint(results)
            _print_case_progress(index, len(cases), results[-1])
            flush_traces()
    finally:
        flush_traces()

    summary = _summary(results)
    print(f"Evaluated: {summary['count']}")
    print(f"Faithfulness: {summary['avg_faithfulness']}")
    print(f"Relevance:    {summary['avg_relevance']}")
    print(f"Correctness:  {summary['avg_correctness']}")
    if summary["failed"]:
        print(f"Failed:       {summary['failed']}")
        print(f"Failure reasons: {summary['failure_reasons']}")
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run compact Haiku answer evaluation over the golden set.")
    parser.add_argument("--limit", type=int, help="Evaluate only the first N golden-set cases.")
    parser.add_argument("--offset", type=int, default=0, help="Skip the first N golden-set cases before applying --limit.")
    args = parser.parse_args(argv)
    try:
        run(limit=args.limit, offset=args.offset)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
