#!/usr/bin/env python3
"""Validate the liveness/readiness contract in rendered KubeMind manifests."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import yaml


EXPECTED_DEPLOYMENTS = {
    "kubemind-orchestrator",
    "kubemind-retriever",
    "kubemind-answer",
}


def _http_probe_is_valid(probe: object, name: str, deployment: str) -> str | None:
    if not isinstance(probe, dict):
        return f"{deployment}: missing {name}Probe"
    http_get = probe.get("httpGet")
    if not isinstance(http_get, dict):
        return f"{deployment}: {name}Probe must use httpGet"
    if http_get.get("path") != "/health" or http_get.get("port") != "http":
        return f"{deployment}: {name}Probe must target /health on the named http port"
    for field in ("initialDelaySeconds", "periodSeconds", "timeoutSeconds", "failureThreshold"):
        value = probe.get(field)
        if not isinstance(value, int) or value < 1:
            return f"{deployment}: {name}Probe.{field} must be a positive integer"
    return None


def verify(path: Path) -> list[str]:
    documents = list(yaml.safe_load_all(path.read_text(encoding="utf-8")))
    found: set[str] = set()
    errors: list[str] = []
    for document in documents:
        if not isinstance(document, dict) or document.get("kind") != "Deployment":
            continue
        metadata = document.get("metadata")
        name = metadata.get("name") if isinstance(metadata, dict) else None
        if name not in EXPECTED_DEPLOYMENTS:
            continue
        found.add(name)
        try:
            containers: Any = document["spec"]["template"]["spec"]["containers"]
        except (KeyError, TypeError):
            errors.append(f"{name}: missing pod containers")
            continue
        if not isinstance(containers, list) or len(containers) != 1:
            errors.append(f"{name}: expected exactly one application container")
            continue
        container = containers[0]
        if not isinstance(container, dict):
            errors.append(f"{name}: malformed application container")
            continue
        for probe_name in ("readiness", "liveness"):
            error = _http_probe_is_valid(container.get(f"{probe_name}Probe"), probe_name, name)
            if error:
                errors.append(error)

    missing = EXPECTED_DEPLOYMENTS - found
    errors.extend(f"missing deployment {name}" for name in sorted(missing))
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify KubeMind workload health probes.")
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    errors = verify(args.manifest)
    if errors:
        raise SystemExit("Kubernetes probe gate failed:\n- " + "\n- ".join(errors))
    print("Kubernetes probe gate passed for orchestrator, retriever, and answer.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
