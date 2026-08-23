"""Constrained Anthropic server-side search for platform reference evidence."""

from __future__ import annotations

from typing import Any


# Claude executes this server tool. The application never accepts a user URL or
# exposes a generic HTTP client; the provider enforces this documentation allowlist.
PLATFORM_REFERENCE_SEARCH_TOOL: dict[str, Any] = {
    "type": "web_search_20250305",
    "name": "web_search",
    "max_uses": 3,
    "allowed_domains": [
        "aws.amazon.com",
        "docs.aws.amazon.com",
        "learn.microsoft.com",
        "cloud.google.com",
        "docs.cloud.google.com",
        "kubernetes.io",
        "helm.sh",
        "argo-cd.readthedocs.io",
        "fluxcd.io",
        "backstage.io",
        "istio.io",
        "linkerd.io",
        "cert-manager.io",
        "karpenter.sh",
        "prometheus.io",
        "grafana.com",
        "opentelemetry.io",
        "docs.nvidia.com",
        "docs.vllm.ai",
        "docs.ray.io",
        "kubeflow.org",
        "mlflow.org",
    ],
}

__all__ = ["PLATFORM_REFERENCE_SEARCH_TOOL"]
