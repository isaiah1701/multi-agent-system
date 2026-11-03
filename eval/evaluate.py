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
from urllib.parse import urlsplit
from uuid import uuid4

from agents.orchestrator.orchestrator import invoke
from agents.orchestrator.llm.client import create_message
from agents.orchestrator.llm.config import JUDGE_MAX_TOKENS, JUDGE_MODEL
from serving.app.langfuse import (
    flush_traces,
    request_trace,
    score_current_trace,
    update_current_span,
    update_trace_span,
)


EVAL_DIRECTORY = Path(__file__).resolve().parent
GOLDEN_SET_PATH = EVAL_DIRECTORY / "golden_set.jsonl"
RESULTS_PATH = EVAL_DIRECTORY / "results.jsonl"
SUMMARY_PATH = EVAL_DIRECTORY / "summary.json"
METRICS = ("faithfulness", "relevance", "correctness")
MAX_JUDGE_CONTEXT_CHARS = 1_200
CALCULATION_VALUE_ALIASES = {
    "numerator": ("numerator", "part"),
    "denominator": ("denominator", "total"),
    "previous": ("previous", "old", "old_value"),
    "current": ("current", "new", "new_value"),
    "failures": ("failures", "errors"),
    "requests": ("requests", "total"),
    "successful": ("successful", "available"),
    "used": ("used",),
    "limit": ("limit",),
    "unavailable": ("unavailable", "unavailable_replicas"),
    "total_replicas": ("total_replicas", "total"),
}
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
        for name in ("is_relevant", "answer_requirements", "calculation", "required_source_types")
        if name in expected
    }
    return json.dumps(reference, ensure_ascii=False, separators=(",", ":")) if reference else None


def _evaluation_context(state: Mapping[str, Any]) -> str | None:
    """Give the judge retrieved evidence and public source metadata for source-only turns."""
    parts: list[str] = []
    sources = state.get("sources")
    if isinstance(sources, list):
        for source in sources:
            if not isinstance(source, Mapping):
                continue
            metadata = {
                key: source[key]
                for key in ("id", "type", "title", "source", "url")
                if source.get(key) is not None
            }
            if metadata:
                parts.append("SOURCE METADATA: " + json.dumps(metadata, ensure_ascii=False, separators=(",", ":")))
    context = state.get("context")
    if isinstance(context, str) and context.strip():
        parts.append(context.strip())
    return "\n\n".join(parts) or None


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
        observation_name="llm-as-judge",
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


def _contract_failures(case: Mapping[str, Any], state: Mapping[str, Any]) -> list[str]:
    """Check case-declared routing and calculation expectations deterministically."""
    expected = case.get("expected")
    if not isinstance(expected, Mapping):
        return []
    failures: list[str] = []
    summary = _tool_summary(state)
    tool_names = summary["tool_names"]
    for name in expected.get("required_tools", []):
        if name not in tool_names:
            failures.append(f"missing_required_tool:{name}")
    for name in expected.get("forbidden_tools", []):
        if name in tool_names:
            failures.append(f"forbidden_tool_used:{name}")
    expected_relevance = expected.get("is_relevant")
    if isinstance(expected_relevance, bool) and state.get("is_relevant") is not expected_relevance:
        failures.append(f"is_relevant_mismatch:expected_{str(expected_relevance).lower()}")
    sources = state.get("sources")
    source_types = {
        source.get("type")
        for source in sources
        if isinstance(source, Mapping) and isinstance(source.get("type"), str)
    } if isinstance(sources, list) else set()
    for source_type in expected.get("required_source_types", []):
        if source_type not in source_types:
            failures.append(f"missing_required_source_type:{source_type}")
    for forbidden_domain in expected.get("forbidden_source_domains", []):
        for source in sources if isinstance(sources, list) else []:
            if not isinstance(source, Mapping) or not isinstance(source.get("url"), str):
                continue
            hostname = urlsplit(source["url"]).hostname or ""
            if hostname.casefold() == str(forbidden_domain).casefold() or hostname.casefold().endswith(
                "." + str(forbidden_domain).casefold()
            ):
                failures.append(f"forbidden_source_domain:{forbidden_domain}")
                break

    tool_results = state.get("tool_results")
    if not isinstance(tool_results, list):
        tool_results = []
    for expected_call in expected.get("expected_tool_calls", []):
        if not isinstance(expected_call, Mapping):
            failures.append("invalid_expected_tool_call")
            continue
        matched = False
        for result in tool_results:
            if not isinstance(result, Mapping):
                continue
            output = result.get("output")
            if isinstance(output, Mapping) and all(output.get(key) == value for key, value in expected_call.items()):
                matched = True
                break
        if not matched:
            failures.append("expected_tool_call_not_found:" + json.dumps(expected_call, sort_keys=True))

    calculation = expected.get("calculation")
    if isinstance(calculation, Mapping):
        expected_result = calculation.get("expected_result")
        operation = calculation.get("operation")
        unit = calculation.get("unit")
        outputs = [
            result
            for result in tool_results
            if isinstance(result, Mapping)
            and result.get("tool_name") == "calculate"
            and isinstance(result.get("output"), Mapping)
        ]
        actual_call = next(
            (item for item in outputs if item["output"].get("operation") == operation),
            None,
        )
        if not isinstance(actual_call, Mapping):
            failures.append(f"calculation_not_run:{operation}")
        else:
            actual = actual_call["output"]
            actual_input = actual_call.get("input")
            actual_values = actual_input.get("values") if isinstance(actual_input, Mapping) else None
            expected_values = calculation.get("values")
            if isinstance(expected_values, Mapping):
                input_mismatch = not isinstance(actual_values, Mapping)
                if isinstance(actual_values, Mapping):
                    for key, expected_value in expected_values.items():
                        aliases = CALCULATION_VALUE_ALIASES.get(key, (key,))
                        actual_value = next((actual_values[name] for name in aliases if name in actual_values), None)
                        if (
                            key == "successful"
                            and actual_value is None
                            and isinstance(actual_values.get("requests", actual_values.get("total")), (int, float))
                        ):
                            failed = actual_values.get("failures", actual_values.get("errors"))
                            if isinstance(failed, (int, float)):
                                actual_value = actual_values.get("requests", actual_values.get("total")) - failed
                        if actual_value != expected_value:
                            input_mismatch = True
                            break
                if input_mismatch:
                    failures.append("calculation_inputs_mismatch")
            actual_result = actual.get("result")
            if (
                isinstance(expected_result, (int, float))
                and not isinstance(expected_result, bool)
                and (
                    not isinstance(actual_result, (int, float))
                    or isinstance(actual_result, bool)
                    or not math.isclose(float(actual_result), float(expected_result), rel_tol=0.0, abs_tol=1e-4)
                )
            ):
                failures.append(f"calculation_result_mismatch:{actual_result}")
            if actual.get("unit") != unit:
                failures.append(f"calculation_unit_mismatch:{actual.get('unit')}")
    return failures


async def _invoke_case(case: Mapping[str, Any], request_id: str) -> Mapping[str, Any]:
    """Run setup turns and the scored question in one in-memory conversation."""
    setup_turns = case.get("setup_turns", [])
    if not setup_turns:
        result = await invoke(str(case["question"]), request_id=request_id)
        return result if isinstance(result, Mapping) else {}
    thread_id = f"eval-{case.get('id') or 'case'}-{request_id}"
    if isinstance(setup_turns, list):
        for index, turn in enumerate(setup_turns):
            question = turn.get("question") if isinstance(turn, Mapping) else turn
            if isinstance(question, str) and question.strip():
                await invoke(question, thread_id=thread_id, request_id=f"{request_id}-setup-{index}")
    result = await invoke(str(case["question"]), thread_id=thread_id, request_id=request_id)
    return result if isinstance(result, Mapping) else {}


def _timing(started_at: float, *, application_ms: int | None = None, judge_ms: int | None = None) -> dict[str, int]:
    timing = {"total": round((time.perf_counter() - started_at) * 1000)}
    if application_ms is not None:
        timing["application"] = application_ms
    if judge_ms is not None:
        timing["judge"] = judge_ms
    return timing


def _evaluate_case(case: Mapping[str, Any], request_id: str) -> dict[str, Any]:
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
        state = asyncio.run(_invoke_case(case, request_id))
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

    contract_failures = _contract_failures(case, state)
    if contract_failures:
        result = _case_error(
            case,
            "case_contract_failed",
            stage="contract",
            details={"contract_failures": contract_failures, **_tool_summary(state)},
            timing_ms=_timing(started_at, application_ms=application_ms),
        )
        update_current_span(output=result, level="ERROR", status_message="case_contract_failed")
        return result

    judge_started_at = time.perf_counter()
    try:
        scores = asyncio.run(
            judge_answer(
                question=(
                    "PRIOR TURNS:\n"
                    + "\n".join(
                        str(turn.get("question") if isinstance(turn, Mapping) else turn)
                        for turn in case.get("setup_turns", [])
                    )
                    + "\n\nCURRENT QUESTION:\n"
                    + question
                    if isinstance(case.get("setup_turns"), list) and case.get("setup_turns")
                    else question
                ),
                answer=answer.strip(),
                reference=_reference(case),
                context=_evaluation_context(state),
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


def evaluate_case(case: Mapping[str, Any]) -> dict[str, Any]:
    """Evaluate one case as a correlated trace with judge scores and cost."""
    case_id = str(case.get("id") or f"line-{case.get('_line', 'unknown')}")
    request_id = f"eval-{case_id}-{uuid4().hex}"
    with request_trace(
        request_id,
        name="golden-set-evaluation-case",
        component="eval",
        input={"case_id": case_id, "question": case.get("question")},
        tags=["eval", "llm-as-judge", str(case.get("category") or "uncategorized")],
    ) as trace:
        result = _evaluate_case(case, request_id)
        update_trace_span(trace, output=result, metadata={"request_id": request_id, "case_id": case_id})
        for metric in METRICS:
            value = result.get(metric)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                score_current_trace(metric, float(value), metadata={"case_id": case_id})
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
