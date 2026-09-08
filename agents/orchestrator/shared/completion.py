"""Helpers that keep token-limited model responses presentable."""

import re


_COMPLETE_SENTENCE = re.compile(r"[.!?](?:\s*\[\d+\])*")


def trim_incomplete_final_sentence(text: str) -> str:
    """Remove a provider-cutoff fragment while preserving complete sentences."""
    normalized = text.strip()
    if not normalized:
        return normalized
    # A cutoff such as ``(e.`` contains a period but is not a complete
    # sentence. Discard from the earliest unmatched delimiter before looking
    # for the last genuine sentence boundary.
    delimiters = {"(": ")", "[": "]", "{": "}"}
    stack: list[tuple[str, int]] = []
    for index, character in enumerate(normalized):
        if character in delimiters:
            stack.append((character, index))
        elif character in delimiters.values() and stack and delimiters[stack[-1][0]] == character:
            stack.pop()
    if stack:
        normalized = normalized[: stack[0][1]].rstrip()
    if re.search(r"(?:\s*\[\d+\])+\s*$", normalized):
        return normalized
    sentence_ends = list(_COMPLETE_SENTENCE.finditer(normalized))
    if not sentence_ends:
        return normalized
    final_sentence_end = sentence_ends[-1].end()
    return normalized if final_sentence_end == len(normalized) else normalized[:final_sentence_end].strip()
