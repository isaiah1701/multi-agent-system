#!/usr/bin/env python3
"""Fail CI when the production golden-set evaluation regresses."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the persisted golden-set evaluation summary.")
    parser.add_argument("--summary", type=Path, default=Path("eval/summary.json"))
    parser.add_argument("--min-faithfulness", type=float, default=0.85)
    parser.add_argument("--min-relevance", type=float, default=0.85)
    parser.add_argument("--min-correctness", type=float, default=0.85)
    args = parser.parse_args()

    try:
        summary = json.loads(args.summary.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"Unable to read golden-set summary: {error}") from error
    if not isinstance(summary, dict):
        raise SystemExit("Golden-set summary must be a JSON object.")

    attempted = summary.get("attempted")
    scored = summary.get("count")
    failed = summary.get("failed")
    if not isinstance(attempted, int) or attempted < 1:
        raise SystemExit("Golden-set evaluation did not attempt any cases.")
    if scored != attempted or failed != 0:
        raise SystemExit(
            f"Golden-set evaluation is incomplete: attempted={attempted}, scored={scored}, failed={failed}."
        )

    thresholds = {
        "avg_faithfulness": args.min_faithfulness,
        "avg_relevance": args.min_relevance,
        "avg_correctness": args.min_correctness,
    }
    failures: list[str] = []
    for metric, minimum in thresholds.items():
        value = summary.get(metric)
        if not isinstance(value, (int, float)) or value < minimum:
            failures.append(f"{metric}={value!r} is below {minimum:.2f}")
    if failures:
        raise SystemExit("Golden-set quality gate failed: " + "; ".join(failures))

    print(
        "Golden-set quality gate passed: "
        + ", ".join(f"{metric}={summary[metric]:.4f}" for metric in thresholds)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
