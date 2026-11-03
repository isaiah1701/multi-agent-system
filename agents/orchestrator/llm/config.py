"""Anthropic model roles for the Kubernetes RAG workflow."""

from __future__ import annotations

import os

from dotenv import load_dotenv


load_dotenv()

# Keep model IDs fixed across local, evaluation, and deployed runs. Anthropic's
# dateless Sonnet 5 ID is a pinned snapshot; Haiku 4.5 uses its dated snapshot ID.
CONTEXT_MODEL = "claude-haiku-4-5-20251001"
ANSWER_MODEL = "claude-sonnet-5"
JUDGE_MODEL = CONTEXT_MODEL
CONTEXT_MAX_TOKENS = int(os.getenv("CONTEXT_MAX_TOKENS", "300"))
# Standard answers use a restrained cap and a smaller word target. The answer
# node can use the extended cap only for explicitly detailed or urgent work.
ANSWER_MAX_TOKENS = int(os.getenv("ANSWER_MAX_TOKENS", "400"))
ANSWER_EXTENDED_MAX_TOKENS = max(
    ANSWER_MAX_TOKENS,
    int(os.getenv("ANSWER_EXTENDED_MAX_TOKENS", "900")),
)


def _target_words(name: str, default: int, maximum_tokens: int) -> int:
    """Reserve token headroom for a complete answer, citations, and punctuation."""
    configured = int(os.getenv(name, str(default)))
    return min(max(1, configured), max(1, (maximum_tokens - 80) * 3 // 4))


ANSWER_TARGET_WORDS = _target_words("ANSWER_TARGET_WORDS", 200, ANSWER_MAX_TOKENS)
ANSWER_EXTENDED_TARGET_WORDS = _target_words(
    "ANSWER_EXTENDED_TARGET_WORDS", 360, ANSWER_EXTENDED_MAX_TOKENS
)
JUDGE_MAX_TOKENS = int(os.getenv("JUDGE_MAX_TOKENS", "80"))
# Haiku is used only after a deterministic output check requests review.
OUTPUT_GUARD_JUDGE_MODEL = CONTEXT_MODEL
OUTPUT_GUARD_JUDGE_MAX_TOKENS = int(os.getenv("OUTPUT_GUARD_JUDGE_MAX_TOKENS", "48"))
# Haiku is used only when a question has no deterministic Kubernetes or
# platform-infrastructure signal. It must identify questions that are plainly
# out of scope; ambiguous wording is intentionally allowed through.
INPUT_GUARD_JUDGE_MODEL = CONTEXT_MODEL
INPUT_GUARD_JUDGE_MAX_TOKENS = int(os.getenv("INPUT_GUARD_JUDGE_MAX_TOKENS", "32"))
PROMPT_CACHING_ENABLED = os.getenv("PROMPT_CACHING_ENABLED", "true").strip().casefold() not in {"0", "false", "no"}
