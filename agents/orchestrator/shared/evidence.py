"""Normalize tool output into small, durable pieces of answer evidence."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, TypedDict


# Retrieval may consider more candidates, but only the strongest few document
# chunks become answer evidence or enter an LLM prompt.
MAX_DOCUMENT_EVIDENCE = 3
MAX_GITHUB_EVIDENCE = 3
MAX_PLATFORM_EVIDENCE = 3
_CONTENT_LIMIT = 2_400

EvidenceType = Literal["kubernetes_docs", "github", "platform_reference", "calculation"]


class EvidenceRecord(TypedDict):
    """Normalized evidence stored in graph state and supplied to answer generation."""

    id: str | None
    type: EvidenceType
    title: str
    source: str
    section: str | None
    url: str | None
    chunk_id: str | None
    content: str | None


def build_evidence(tool_results: object) -> list[EvidenceRecord]:
    """Return display-safe provenance plus private supporting text from tool output.

    The returned records are intentionally plain dictionaries: they pass cleanly
    through LangGraph state, while the serving layer selects only public fields.
    """
    if not isinstance(tool_results, list):
        return []

    evidence: list[EvidenceRecord] = []
    remaining_document_chunks = MAX_DOCUMENT_EVIDENCE
    for result in tool_results:
        if not isinstance(result, Mapping):
            continue
        output = result.get("output")
        if not isinstance(output, Mapping) or output.get("ok") is False or output.get("error"):
            continue
        tool_name = result.get("tool_name")
        if tool_name == "search_kubernetes_docs":
            chunks = output.get("chunks")
            if isinstance(chunks, list) and remaining_document_chunks:
                for chunk in chunks[:remaining_document_chunks]:
                    if isinstance(chunk, Mapping):
                        evidence.append(_documentation_evidence(chunk))
                remaining_document_chunks -= min(len(chunks), remaining_document_chunks)
        elif tool_name == "github_kubernetes_lookup":
            evidence.extend(_github_evidence(output)[:MAX_GITHUB_EVIDENCE])
        elif tool_name == "platform_reference_lookup":
            evidence.extend(_platform_evidence(output)[:MAX_PLATFORM_EVIDENCE])
        elif tool_name == "calculate":
            calculation = _calculation_evidence(output, result.get("input"))
            if calculation:
                evidence.append(calculation)

    return _assign_ids_and_dedupe(evidence)


def format_evidence_for_prompt(evidence: object) -> str:
    """Render the compact evidence contract used by the context and answer models."""
    if not isinstance(evidence, list) or not evidence:
        return "No sourced evidence is available."

    blocks: list[str] = []
    for item in evidence:
        if not isinstance(item, Mapping):
            continue
        source_id = _text(item.get("id"))
        title = _text(item.get("title"))
        if not source_id or not title:
            continue
        fields = [f"[{source_id}]", f"type: {_text(item.get('type'))}", f"title: {title}"]
        for key in ("source", "section", "url"):
            value = _text(item.get(key))
            if value:
                fields.append(f"{key}: {value}")
        content = _text(item.get("content"))
        if content:
            fields.append(f"content: {content[:_CONTENT_LIMIT]}")
        blocks.append("\n".join(fields))
    return "\n\n".join(blocks) or "No sourced evidence is available."


def source_follow_up_answer(evidence: object) -> str:
    """Return a non-factual response; the API/UI render the structured list."""
    return "Sources from the previous answer:"


def _documentation_evidence(chunk: Mapping[str, Any]) -> EvidenceRecord:
    source = _text(chunk.get("source")) or "Kubernetes documentation"
    section = _text(chunk.get("section"))
    title = (section.split(">", 1)[0].strip() if section else "") or _title_from_source(source)
    return {
        "id": None,
        "type": "kubernetes_docs",
        "title": title,
        "source": source,
        "section": section,
        "url": None,
        "chunk_id": _text(chunk.get("chunk_id")),
        "content": _text(chunk.get("text")),
    }


def _github_evidence(output: Mapping[str, Any]) -> list[EvidenceRecord]:
    evidence: list[EvidenceRecord] = []
    for key in ("release", "releases", "issues", "pull_requests"):
        records = output.get(key)
        if isinstance(records, Mapping):
            records = [records]
        if not isinstance(records, list):
            continue
        for record in records:
            if not isinstance(record, Mapping):
                continue
            url = _text(record.get("html_url"))
            if not url:
                continue
            name = _text(record.get("tag_name")) or _text(record.get("name")) or _text(record.get("title"))
            if not name:
                name = "Kubernetes GitHub result"
            evidence.append(
                {
                    "id": None,
                    "type": "github",
                    "title": f"Kubernetes GitHub — {name}",
                    "source": "GitHub",
                    "section": None,
                    "url": url,
                    "chunk_id": None,
                    "content": _text(record.get("body")) or _text(record.get("title")),
                }
            )

    changelog = output.get("changelog")
    if not isinstance(changelog, Mapping) and output.get("resource_type") == "changelog":
        changelog = output
    if isinstance(changelog, Mapping) and _text(changelog.get("html_url")):
        version = _text(changelog.get("version")) or "Kubernetes changelog"
        evidence.append(
            {
                "id": None,
                "type": "github",
                "title": f"Kubernetes GitHub — {version}",
                "source": "GitHub",
                "section": _text(changelog.get("path")),
                "url": _text(changelog.get("html_url")),
                "chunk_id": None,
                "content": _text(changelog.get("content")),
            }
        )
    tags = output.get("tags")
    if isinstance(tags, list):
        for tag in tags:
            if not isinstance(tag, Mapping):
                continue
            url = _text(tag.get("zipball_url"))
            name = _text(tag.get("name"))
            if not url or not name:
                continue
            evidence.append(
                {
                    "id": None,
                    "type": "github",
                    "title": f"Kubernetes GitHub — tag {name}",
                    "source": "GitHub",
                    "section": None,
                    "url": url,
                    "chunk_id": None,
                    "content": _text(tag.get("commit_sha")),
                }
            )
    return evidence


def _platform_evidence(output: Mapping[str, Any]) -> list[EvidenceRecord]:
    records = output.get("sources")
    if not isinstance(records, list):
        return []
    evidence: list[EvidenceRecord] = []
    for record in records:
        if not isinstance(record, Mapping):
            continue
        url = _text(record.get("url"))
        title = _text(record.get("title"))
        if not url or not title:
            continue
        evidence.append(
            {
                "id": None,
                "type": "platform_reference",
                "title": title,
                "source": _provider_from_url(url),
                "section": None,
                "url": url,
                "chunk_id": None,
                "content": _text(record.get("excerpt")) or _text(output.get("summary")),
            }
        )
    return evidence


def _calculation_evidence(output: Mapping[str, Any], tool_input: object) -> EvidenceRecord | None:
    operation = _text(output.get("operation"))
    result = output.get("result")
    unit = _text(output.get("unit"))
    if not operation or isinstance(result, bool) or not isinstance(result, (int, float)):
        return None
    values = tool_input.get("values") if isinstance(tool_input, Mapping) else None
    expression = _calculation_expression(operation, values, result, unit)
    return {
        "id": None,
        "type": "calculation",
        "title": f"Calculation — {expression}",
        "source": "Calculator",
        "section": None,
        "url": None,
        "chunk_id": None,
        "content": expression,
    }


def _calculation_expression(operation: str, values: object, result: int | float, unit: str | None) -> str:
    """Describe operands when the tool call retained them; otherwise stay honest."""
    if not isinstance(values, Mapping):
        return f"{operation.replace('_', ' ')} = {result:g}{unit or ''}"

    def value(*names: str) -> int | float | None:
        for name in names:
            candidate = values.get(name)
            if isinstance(candidate, (int, float)) and not isinstance(candidate, bool):
                return candidate
        return None

    numerator: int | float | None
    denominator: int | float | None
    if operation == "percentage":
        numerator, denominator = value("numerator", "part"), value("denominator", "total")
    elif operation == "resource_utilization":
        numerator, denominator = value("used"), value("limit")
    elif operation == "error_rate":
        numerator, denominator = value("failures", "errors"), value("requests", "total")
    elif operation == "replica_percentage":
        numerator, denominator = value("unavailable", "unavailable_replicas"), value("total_replicas", "total")
    elif operation == "availability":
        denominator = value("requests", "total")
        numerator = value("successful", "available")
        if numerator is None:
            failures = value("failures", "errors")
            if failures is not None and denominator is not None:
                return f"100 − ({failures:g} / {denominator:g} × 100) = {result:g}{unit or ''}"
    elif operation == "percentage_change":
        previous, current = value("previous", "old", "old_value"), value("current", "new", "new_value")
        if previous is not None and current is not None:
            return f"({current:g} − {previous:g}) / {previous:g} × 100 = {result:g}{unit or ''}"
        numerator = denominator = None
    else:
        numerator = denominator = None

    if numerator is not None and denominator is not None:
        return f"{numerator:g} / {denominator:g} × 100 = {result:g}{unit or ''}"
    return f"{operation.replace('_', ' ')} = {result:g}{unit or ''}"


def _assign_ids_and_dedupe(evidence: list[EvidenceRecord]) -> list[EvidenceRecord]:
    unique: list[EvidenceRecord] = []
    seen: set[tuple[str | None, ...]] = set()
    for item in evidence:
        identity = tuple(item.get(key) for key in ("type", "title", "source", "section", "url", "chunk_id"))
        if identity in seen:
            continue
        seen.add(identity)
        normalized = dict(item)
        normalized["id"] = str(len(unique) + 1)
        unique.append(normalized)
    return unique


def _title_from_source(source: str) -> str:
    filename = source.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    return filename.replace("-", " ").replace("_", " ").title()


def _provider_from_url(url: str) -> str:
    host = url.split("//", 1)[-1].split("/", 1)[0].removeprefix("www.")
    return host or "Platform documentation"


def _text(value: object) -> str | None:
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return None
