"""Hybrid retrieval and reranking for the Kubernetes documentation corpus."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .retrieve import RetrievalCandidate


def retrieve(query: str, k: int = 10) -> list["RetrievalCandidate"]:
    """Run the complete default retrieval pipeline without importing CLI code eagerly."""
    from .retrieve import retrieve as run_retrieval

    return run_retrieval(query, k=k)


__all__ = ["retrieve"]
