"""Helpers that keep token-limited model responses presentable."""

import re


_COMPLETE_SENTENCE = re.compile(r"[.!?](?:\s*\[\d+\])*")


def trim_incomplete_final_sentence(text: str) -> str:
    """Remove a provider-cutoff fragment while preserving complete sentences."""
    normalized = text.strip()
    if not normalized:
        return normalized
    sentence_ends = list(_COMPLETE_SENTENCE.finditer(normalized))
    if not sentence_ends:
        return normalized
    final_sentence_end = sentence_ends[-1].end()
    return normalized if final_sentence_end == len(normalized) else normalized[:final_sentence_end].strip()
