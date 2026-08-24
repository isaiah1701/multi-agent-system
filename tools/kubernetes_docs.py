"""Agent-facing access to the existing Kubernetes documentation retriever."""

from __future__ import annotations

from typing import Any

from retrieval import retrieve
from retrieval.retrieve import RetrievalCandidate
from observability import observe


def _serialize_candidate(candidate: RetrievalCandidate) -> dict[str, Any]:
    """Preserve the retrieval result fields that are useful to an answering model."""
    return {
        "chunk_id": candidate.chunk_id,
        "text": candidate.text,
        "source": candidate.source_document,
        "section": candidate.section,
        "reranker_score": candidate.rerank_score,
    }


@observe(name="kubernetes-document-search", as_type="tool")
def search_kubernetes_docs(query: str, k: int = 10) -> dict[str, Any]:
    """Search the persisted Kubernetes corpus through its public hybrid retrieval API."""
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be a non-empty string")
    if isinstance(k, bool) or not isinstance(k, int) or not 1 <= k <= 10:
        raise ValueError("k must be an integer between 1 and 10")

    candidates = retrieve(query.strip(), k=k)
    return {
        "query": query.strip(),
        "chunks": [_serialize_candidate(candidate) for candidate in candidates],
    }
