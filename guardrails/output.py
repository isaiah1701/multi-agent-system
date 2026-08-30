"""Deterministic output checks for final Kubernetes assistant answers."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from serving.app.langfuse import observe, update_current_span


OutputDecision = Literal["allow", "block", "review"]

# These patterns are intentionally narrow: normal technical use of words such
# as "token" or "error" must not cause a false block.
_SENSITIVE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("anthropic_api_key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{12,}\b")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b", re.IGNORECASE)),
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    (
        "credential_assignment",
        re.compile(
            r"\b(?:ANTHROPIC_API_KEY|GITHUB_TOKEN|LANGFUSE_(?:PUBLIC|SECRET)_KEY|AWS_SECRET_ACCESS_KEY)\s*=\s*\S+",
            re.IGNORECASE,
        ),
    ),
)
_STACK_TRACE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"Traceback \(most recent call last\):"),
    re.compile(r'File "[^"\n]+", line \d+'),
)
_ATTRIBUTION_PATTERN = re.compile(
    r"https?://|\b(?:source|sources|according to|kubernetes (?:docs|documentation)|github (?:release|repository))\b",
    re.IGNORECASE,
)
_EXTERNAL_EVIDENCE_TOOLS = frozenset(
    {"search_kubernetes_docs", "github_kubernetes_lookup", "platform_reference_lookup"}
)
_CITATION_PATTERN = re.compile(r"\[(\d+)\]")
INSUFFICIENT_EVIDENCE_MESSAGE = "I don't have enough sourced evidence to answer that reliably."


@dataclass(frozen=True)
class OutputGuardResult:
    """The deterministic gate outcome, without retaining the answer text."""

    decision: OutputDecision
    reasons: tuple[str, ...] = ()


@observe(name="output-guardrail", as_type="guardrail", capture_input=False, capture_output=False)
def inspect_output(
    answer: str,
    tool_results: list[Mapping[str, object]] | object,
    sources: list[Mapping[str, object]] | object | None = None,
) -> OutputGuardResult:
    """Block unsafe output and deterministically validate supplied citation IDs."""
    normalized = answer.strip() if isinstance(answer, str) else ""
    if not normalized:
        return _result("block", "empty_answer")
    if len(normalized) > 12_000:
        return _result("block", "answer_too_long")

    sensitive_reasons = [name for name, pattern in _SENSITIVE_PATTERNS if pattern.search(normalized)]
    if sensitive_reasons:
        return _result("block", *sensitive_reasons)
    if any(pattern.search(normalized) for pattern in _STACK_TRACE_PATTERNS):
        return _result("block", "internal_stack_trace")

    citation_result = _citation_result(normalized, sources)
    if citation_result is not None:
        if citation_result.decision == "review":
            return _result("block", *citation_result.reasons)
        return _result(citation_result.decision, *citation_result.reasons)

    if _has_external_evidence(tool_results) and not _ATTRIBUTION_PATTERN.search(normalized):
        return _result("review", "external_evidence_without_attribution")
    return _result("allow")


def inspect_stream_prefix(
    answer: str,
    tool_results: list[Mapping[str, object]] | object,
    sources: list[Mapping[str, object]] | object | None = None,
) -> OutputGuardResult:
    """Check an unfinalized answer prefix without emitting telemetry for every token.

    This applies the hard disclosure checks before a prefix is released to a
    browser. It intentionally holds evidence-backed text until it has visible
    attribution; the normal final-answer gate still runs on the full response.
    """
    normalized = answer.strip() if isinstance(answer, str) else ""
    if len(normalized) > 12_000:
        return OutputGuardResult("block", ("answer_too_long",))
    sensitive_reasons = [name for name, pattern in _SENSITIVE_PATTERNS if pattern.search(normalized)]
    if sensitive_reasons:
        return OutputGuardResult("block", tuple(sensitive_reasons))
    if any(pattern.search(normalized) for pattern in _STACK_TRACE_PATTERNS):
        return OutputGuardResult("block", ("internal_stack_trace",))
    citation_result = _citation_result(normalized, sources)
    if citation_result is not None:
        return citation_result
    if _has_external_evidence(tool_results) and not _ATTRIBUTION_PATTERN.search(normalized):
        return OutputGuardResult("review", ("external_evidence_without_attribution",))
    return OutputGuardResult("allow")


def parse_backup_judge_allow(response: object) -> bool | None:
    """Read the backup judge's tiny JSON response without accepting prose guesses."""
    text = _response_text(response)
    if not text:
        return None
    text = text.strip()
    if text.startswith("```") and text.endswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    candidates = [text]
    match = re.search(r"\{[^{}]*\}", text, flags=re.DOTALL)
    if match:
        candidates.append(match.group(0))
    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except (TypeError, ValueError):
            continue
        if isinstance(payload, Mapping) and isinstance(payload.get("allow"), bool):
            return payload["allow"]
    return None


def _response_text(response: object) -> str:
    content = response.get("content") if isinstance(response, Mapping) else getattr(response, "content", None)
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        text = block.get("text") if isinstance(block, Mapping) else getattr(block, "text", None)
        if isinstance(text, str):
            parts.append(text)
    return "".join(parts)


def _has_external_evidence(tool_results: object) -> bool:
    if not isinstance(tool_results, list):
        return False
    for result in tool_results:
        if not isinstance(result, Mapping) or result.get("tool_name") not in _EXTERNAL_EVIDENCE_TOOLS:
            continue
        output = result.get("output")
        if not isinstance(output, Mapping):
            continue
        if output.get("ok") is False or output.get("error"):
            continue
        return True
    return False


def _citation_result(answer: str, sources: object) -> OutputGuardResult | None:
    """Validate citations only when the caller supplied first-class evidence.

    Keeping ``None`` distinct from an empty list preserves the legacy generic
    attribution review for callers that have not yet adopted structured sources.
    """
    if sources is None:
        return None
    if answer == INSUFFICIENT_EVIDENCE_MESSAGE:
        return OutputGuardResult("allow")
    if not isinstance(sources, list) or not sources:
        return OutputGuardResult("block", ("missing_evidence",))

    valid_ids: set[str] = set()
    for source in sources:
        if not isinstance(source, Mapping):
            return OutputGuardResult("block", ("missing_source_metadata",))
        source_id = source.get("id")
        source_type = source.get("type")
        title = source.get("title")
        provenance = source.get("source") or source.get("url")
        if (
            not isinstance(source_id, str)
            or not source_id.isdecimal()
            or not isinstance(source_type, str)
            or not source_type.strip()
            or not isinstance(title, str)
            or not title.strip()
            or not isinstance(provenance, str)
            or not provenance.strip()
        ):
            return OutputGuardResult("block", ("missing_source_metadata",))
        valid_ids.add(source_id)

    cited_ids = set(_CITATION_PATTERN.findall(answer))
    if not cited_ids:
        return OutputGuardResult("review", ("missing_citation",))
    unknown = cited_ids - valid_ids
    if unknown:
        return OutputGuardResult("block", ("invalid_citation",))
    return OutputGuardResult("allow")


def _result(decision: OutputDecision, *reasons: str) -> OutputGuardResult:
    result = OutputGuardResult(decision=decision, reasons=tuple(reasons))
    update_current_span(output={"decision": result.decision, "reasons": list(result.reasons)})
    return result
