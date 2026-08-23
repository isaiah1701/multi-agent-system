"""Anthropic model roles for the Kubernetes RAG workflow."""

from __future__ import annotations

import os

from dotenv import load_dotenv


load_dotenv()

# Environment overrides let deployments select models without changing code.
CONTEXT_MODEL = os.getenv("CONTEXT_MODEL", "claude-haiku-4-5-20251001")
ANSWER_MODEL = os.getenv("ANSWER_MODEL", "claude-sonnet-5")
CONTEXT_MAX_TOKENS = int(os.getenv("CONTEXT_MAX_TOKENS", "300"))
# Platform comparisons and evidence-backed trade-off recommendations need more
# room than a short documentation definition. Deployments can still override it.
ANSWER_MAX_TOKENS = int(os.getenv("ANSWER_MAX_TOKENS", "600"))
