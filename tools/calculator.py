"""Deterministic percentage calculations for Kubernetes-related questions."""

from __future__ import annotations

from numbers import Real
from typing import Any


SUPPORTED_OPERATIONS = frozenset(
    {
        "percentage",
        "percentage_change",
        "error_rate",
        "availability",
        "resource_utilization",
        "replica_percentage",
    }
)


def _number(values: dict[str, Any], name: str, *aliases: str) -> float:
    for key in (name, *aliases):
        if key in values:
            value = values[key]
            if isinstance(value, bool) or not isinstance(value, Real):
                raise ValueError(f"values.{key} must be a number")
            return float(value)
    aliases_text = ", ".join((name, *aliases))
    raise ValueError(f"values must include one of: {aliases_text}")


def _percentage(numerator: float, denominator: float, *, denominator_name: str) -> float:
    if denominator == 0:
        raise ValueError(f"values.{denominator_name} must not be zero")
    return round((numerator / denominator) * 100, 4)


def calculate(operation: str, values: dict[str, Any]) -> dict[str, Any]:
    """Perform an explicitly defined calculation without evaluating model-provided code."""
    if operation not in SUPPORTED_OPERATIONS:
        supported = ", ".join(sorted(SUPPORTED_OPERATIONS))
        raise ValueError(f"Unsupported operation {operation!r}. Supported operations: {supported}")
    if not isinstance(values, dict):
        raise ValueError("values must be an object")

    if operation == "percentage":
        result = _percentage(
            _number(values, "numerator", "part"),
            _number(values, "denominator", "total"),
            denominator_name="denominator",
        )
    elif operation == "percentage_change":
        previous = _number(values, "previous", "old", "old_value")
        current = _number(values, "current", "new", "new_value")
        result = _percentage(current - previous, previous, denominator_name="previous")
    elif operation == "error_rate":
        result = _percentage(
            _number(values, "failures", "errors"),
            _number(values, "requests", "total"),
            denominator_name="requests",
        )
    elif operation == "availability":
        requests = _number(values, "requests", "total")
        if any(key in values for key in ("successful", "available")):
            result = _percentage(_number(values, "successful", "available"), requests, denominator_name="requests")
        else:
            result = round(100 - _percentage(_number(values, "failures", "errors"), requests, denominator_name="requests"), 4)
    elif operation == "resource_utilization":
        result = _percentage(_number(values, "used"), _number(values, "limit"), denominator_name="limit")
    else:  # replica_percentage
        result = _percentage(
            _number(values, "unavailable", "unavailable_replicas"),
            _number(values, "total_replicas", "total"),
            denominator_name="total_replicas",
        )

    return {"operation": operation, "result": result, "unit": "%"}
