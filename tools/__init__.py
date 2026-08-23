"""Single registry of the capabilities the Kubernetes agent may invoke."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .calculator import calculate
from .github import github_kubernetes_lookup
from .kubernetes_docs import search_kubernetes_docs
from .platform_references import PLATFORM_REFERENCE_SEARCH_TOOL


ToolFunction = Callable[..., dict[str, Any]]

TOOL_FUNCTIONS: dict[str, ToolFunction] = {
    "search_kubernetes_docs": search_kubernetes_docs,
    "github_kubernetes_lookup": github_kubernetes_lookup,
    "calculate": calculate,
}

# This is Anthropic's native tool schema format. Keep it with the callable registry
# so the agent cannot advertise a tool it is unable to execute.
TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "search_kubernetes_docs",
        "description": "Search the local curated Kubernetes documentation for grounded technical evidence.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Kubernetes documentation search query."},
                "k": {"type": "integer", "minimum": 1, "maximum": 10, "description": "Result count; defaults to 10."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "github_kubernetes_lookup",
        "description": "Look up releases, fixed-version changelogs, issues, pull requests, or tags only for github.com/kubernetes/kubernetes.",
        "input_schema": {
            "type": "object",
            "properties": {
                "resource_type": {
                    "type": "string",
                    "enum": ["latest_release", "releases", "issues", "pull_requests", "tags", "changelog"],
                },
                "query": {"type": "string", "description": "Optional text to search releases, issues, or pull requests; required Kubernetes release tag for changelog."},
                "limit": {"type": "integer", "minimum": 1, "maximum": 20, "description": "Result count; defaults to 5."},
            },
            "required": ["resource_type"],
        },
    },
    {
        "name": "calculate",
        "description": "Perform deterministic percentage calculations. Provide numbers in the same units where applicable.",
        "input_schema": {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": ["percentage", "percentage_change", "error_rate", "availability", "resource_utilization", "replica_percentage"],
                },
                "values": {
                    "type": "object",
                    "description": "percentage: numerator, denominator; percentage_change: previous, current; error_rate: failures, requests; availability: successful, requests; resource_utilization: used, limit; replica_percentage: unavailable, total_replicas.",
                },
            },
            "required": ["operation", "values"],
        },
    },
]

__all__ = [
    "PLATFORM_REFERENCE_SEARCH_TOOL",
    "TOOL_FUNCTIONS",
    "TOOL_SCHEMAS",
    "calculate",
    "github_kubernetes_lookup",
    "search_kubernetes_docs",
]
