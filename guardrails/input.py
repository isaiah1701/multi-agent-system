"""Local, layered relevance guardrail for Kubernetes platform questions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .domain_terms import (
    ADJACENT_TERMS,
    AI_PLATFORM_TERMS,
    ALIASES,
    CLEARLY_IRRELEVANT_PHRASES,
    CLEARLY_IRRELEVANT_TERMS,
    ECOSYSTEM_TERMS,
    KUBERNETES_TERMS,
    MANAGED_KUBERNETES_TERMS,
    PLATFORM_CONTEXT_TERMS,
)


TOKEN_PATTERN = re.compile(r"[a-z0-9]+")
HEADING_PATTERN = re.compile(r"^#{1,3}\s+(.+?)\s*$", re.MULTILINE)
CAMEL_CASE_PATTERN = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
MARKUP_PATTERN = re.compile(r"\{\{.*?\}\}|\[([^\]]+)\]\([^)]*\)|<[^>]+>")
CORPUS_ROOT = Path(__file__).resolve().parents[1] / "corpus" / "kubernetes"


@dataclass(frozen=True)
class RelevanceResult:
    """Internal classification details; only ``allowed`` controls graph routing."""

    allowed: bool
    confidence: float
    matched_domains: tuple[str, ...]


def normalize_query(text: str) -> str:
    """Normalise casing, camel case, punctuation, possessives, and hyphenation."""
    # Preserve acronym plurals (for example, PDBs and PVCs) before splitting
    # CamelCase resource names such as PodDisruptionBudget.
    acronym_safe = re.sub(r"\b([A-Z]{2,})s\b", r"\1S", text)
    expanded = CAMEL_CASE_PATTERN.sub(" ", acronym_safe)
    expanded = expanded.replace("’", "'").casefold()
    expanded = re.sub(r"\b([a-z0-9]+)'s\b", r"\1", expanded)
    return " ".join(TOKEN_PATTERN.findall(expanded))


def _term_matches(normalized: str, term: str) -> bool:
    return f" {term} " in f" {normalized} "


def _matched_terms(normalized: str, terms: frozenset[str]) -> set[str]:
    return {term for term in terms if _term_matches(normalized, term)}


def _token_forms(normalized: str) -> set[str]:
    forms = set(normalized.split())
    for token in tuple(forms):
        if len(token) > 3 and token.endswith("s"):
            forms.add(token[:-1])
    return forms


def _alias_matches(normalized: str) -> set[str]:
    return {
        canonical
        for canonical, variants in ALIASES.items()
        if any(_term_matches(normalized, variant) for variant in variants)
    }


def _clean_heading(raw_heading: str) -> str:
    without_markup = MARKUP_PATTERN.sub(r"\1", raw_heading)
    return normalize_query(without_markup.split("{")[0])


@lru_cache(maxsize=1)
def _corpus_phrases() -> frozenset[str]:
    """Build a lightweight cached vocabulary from documentation paths and headings."""
    phrases: set[str] = set()
    if not CORPUS_ROOT.exists():
        return frozenset()
    for document in CORPUS_ROOT.rglob("*.md"):
        if document.name == "_index.md":
            continue
        path_phrase = normalize_query(document.stem)
        if len(path_phrase) >= 5:
            phrases.add(path_phrase)
        try:
            content = document.read_text(encoding="utf-8")
        except OSError:
            continue
        for heading in HEADING_PATTERN.findall(content):
            phrase = _clean_heading(heading)
            words = phrase.split()
            if len(words) > 1 or (words and len(words[0]) >= 8):
                phrases.add(phrase)
    return frozenset(phrases)


def _corpus_matches(normalized: str) -> set[str]:
    """Match multiword docs phrases, tolerating ordinary singular/plural forms.

    The corpus headings use terms such as ``Headless Services``. Operators
    naturally ask for ``headless service``; an exact substring check rejected
    that valid Kubernetes question. Matching is still limited to contiguous
    multiword phrases, so generic words such as ``service`` do not become a
    global allow-list entry.
    """
    return {
        phrase
        for phrase in _corpus_phrases()
        if len(phrase.split()) > 1 and _phrase_matches(normalized, phrase)
    }


def _phrase_matches(query: str, phrase: str) -> bool:
    query_words = query.split()
    phrase_words = phrase.split()
    if len(phrase_words) > len(query_words):
        return False
    for start in range(len(query_words) - len(phrase_words) + 1):
        if all(_word_forms(left) & _word_forms(right) for left, right in zip(query_words[start:], phrase_words)):
            return True
    return False


def _word_forms(word: str) -> set[str]:
    """Keep the original spelling and add only a conservative plural form."""
    forms = {word}
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        forms.add(word[:-1])
    if len(word) > 4 and word.endswith("ies"):
        forms.add(f"{word[:-3]}y")
    return forms


def classify_kubernetes_relevance(question: str) -> RelevanceResult:
    """Classify plausible Kubernetes/platform questions without retrieval or external calls."""
    if not isinstance(question, str) or not question.strip():
        return RelevanceResult(False, 1.0, ())

    normalized = normalize_query(question)
    tokens = _token_forms(normalized)
    if not normalized:
        return RelevanceResult(False, 1.0, ())
    if _matched_terms(normalized, CLEARLY_IRRELEVANT_TERMS) or _matched_terms(normalized, CLEARLY_IRRELEVANT_PHRASES):
        return RelevanceResult(False, 0.98, ("clearly_irrelevant",))

    domains: list[str] = []
    aliases = _alias_matches(normalized)
    core = tokens & KUBERNETES_TERMS
    # Corpus inspection is a fallback vocabulary layer, not work required for
    # already-recognised Kubernetes jargon or API resources.
    corpus = _corpus_matches(normalized) if not aliases and not core else set()
    ecosystem = _matched_terms(normalized, ECOSYSTEM_TERMS)
    managed = _matched_terms(normalized, MANAGED_KUBERNETES_TERMS)
    adjacent = _matched_terms(normalized, ADJACENT_TERMS)
    ai_platform = _matched_terms(normalized, AI_PLATFORM_TERMS)
    context = tokens & PLATFORM_CONTEXT_TERMS

    if aliases or core:
        domains.append("kubernetes")
    if corpus:
        domains.append("kubernetes_docs")
    if ecosystem:
        domains.append("kubernetes_ecosystem")
    if managed:
        domains.append("managed_kubernetes")
    if adjacent:
        domains.append("platform_infrastructure")
    if ai_platform:
        domains.append("ai_platform")

    # Strong platform signals stand alone; adjacent concepts need a deployment or
    # operations context. A remaining weak domain signal is intentionally allowed
    # as ambiguous rather than incorrectly rejecting a concise follow-up.
    if aliases or core or corpus or ecosystem or managed:
        return RelevanceResult(True, 0.9, tuple(domains))
    if (adjacent or ai_platform) and context:
        return RelevanceResult(True, 0.75, tuple(domains))
    if adjacent or ai_platform:
        return RelevanceResult(True, 0.55, tuple(domains))
    return RelevanceResult(False, 0.9, ())


def is_kubernetes_question(question: str) -> bool:
    """Compatibility wrapper used by the LangGraph input guardrail."""
    return classify_kubernetes_relevance(question).allowed
