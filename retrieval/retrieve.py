"""Hybrid ANN and BM25 retrieval over the persisted Kubernetes Chroma collection."""

from __future__ import annotations

import argparse
import logging
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Protocol, Sequence

from ingestion.chunk import ChunkingConfig, SentenceTransformerEmbedder
from ingestion.ingest import DEFAULT_COLLECTION_NAME, DEFAULT_DATABASE_PATH

LOGGER = logging.getLogger(__name__)
TOKEN_PATTERN = re.compile(r"\b[\w-]+\b", re.UNICODE)
DEFAULT_RERANKER_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"


@dataclass(frozen=True)
class RetrievalCandidate:
    """One documentation chunk with scores accumulated during the pipeline."""

    chunk_id: str
    text: str
    source_document: str
    section: str | None
    metadata: dict[str, Any] = field(default_factory=dict)
    ann_score: float | None = None
    ann_distance: float | None = None
    bm25_score: float | None = None
    rrf_score: float | None = None
    rerank_score: float | None = None


@dataclass(frozen=True)
class RerankerConfig:
    """Runtime configuration for the local cross-encoder reranker."""

    model_name: str = DEFAULT_RERANKER_MODEL
    device: str | None = None
    batch_size: int = 16


class CrossEncoderReranker:
    """Lazy Sentence Transformers cross-encoder adapter."""

    def __init__(self, config: RerankerConfig | None = None) -> None:
        self.config = config or RerankerConfig()
        self._model: object | None = None

    def _load_model(self) -> object:
        if self._model is None:
            try:
                from sentence_transformers import CrossEncoder
            except ImportError as error:  # pragma: no cover - environment dependent
                raise RuntimeError(
                    "sentence-transformers is required for reranking. "
                    "Install dependencies with: python3 -m pip install -r requirements.txt"
                ) from error
            LOGGER.info("Loading local cross-encoder reranker: %s", self.config.model_name)
            self._model = CrossEncoder(self.config.model_name, device=self.config.device)
        return self._model

    def rerank(
        self, query: str, candidates: Sequence[RetrievalCandidate]
    ) -> list[RetrievalCandidate]:
        """Score query/chunk pairs and return candidates in descending relevance order."""
        if not candidates:
            return []
        model = self._load_model()
        scores = model.predict(  # type: ignore[attr-defined]
            [(query, candidate.text) for candidate in candidates],
            batch_size=self.config.batch_size,
            show_progress_bar=False,
        )
        ranked = [
            replace(candidate, rerank_score=float(score))
            for candidate, score in zip(candidates, scores, strict=True)
        ]
        return sorted(ranked, key=lambda candidate: candidate.rerank_score or float("-inf"), reverse=True)


class ChromaCollection(Protocol):
    """Subset of the Chroma collection interface used by this module."""

    def count(self) -> int: ...

    def get(self, *, include: list[str]) -> dict[str, Any]: ...

    def query(self, **kwargs: Any) -> dict[str, Any]: ...


@dataclass(frozen=True)
class RetrievalConfig:
    """Configuration for the persistent collection and retrieval stages."""

    database_path: Path = DEFAULT_DATABASE_PATH
    collection_name: str = DEFAULT_COLLECTION_NAME
    embedding_model_name: str = ChunkingConfig.embedding_model_name
    embedding_device: str | None = ChunkingConfig.embedding_device
    embedding_batch_size: int = ChunkingConfig.embedding_batch_size
    ann_candidates: int = 30
    bm25_candidates: int = 30
    hybrid_candidate_limit: int = 10
    rrf_constant: int = 60
    reranker: RerankerConfig = RerankerConfig()

    def __post_init__(self) -> None:
        for name in ("ann_candidates", "bm25_candidates", "hybrid_candidate_limit", "rrf_constant"):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be at least 1")


def tokenize(text: str) -> list[str]:
    """Produce a lightweight, deterministic token stream for BM25."""
    return TOKEN_PATTERN.findall(text.lower())


def _candidate_from_record(
    chunk_id: str, text: str, metadata: dict[str, Any] | None, **scores: float | None
) -> RetrievalCandidate:
    metadata = dict(metadata or {})
    return RetrievalCandidate(
        chunk_id=chunk_id,
        text=text,
        source_document=str(metadata.get("source_document") or metadata.get("relative_path") or "unknown"),
        section=(
            str(metadata["heading_path"])
            if metadata.get("heading_path") is not None
            else (str(metadata["context_path"]) if metadata.get("context_path") is not None else None)
        ),
        metadata=metadata,
        **scores,
    )


def bm25_search(
    query: str, records: Sequence[RetrievalCandidate], limit: int
) -> list[RetrievalCandidate]:
    """Rank persisted records lexically, preserving IDs and source metadata."""
    if not records or limit < 1:
        return []
    try:
        from rank_bm25 import BM25Okapi
    except ImportError as error:  # pragma: no cover - environment dependent
        raise RuntimeError(
            "rank-bm25 is required for keyword retrieval. "
            "Install dependencies with: python3 -m pip install -r requirements.txt"
        ) from error

    index = BM25Okapi([tokenize(record.text) for record in records])
    scores = index.get_scores(tokenize(query))
    ordered = sorted(range(len(records)), key=lambda index: float(scores[index]), reverse=True)
    return [replace(records[index], bm25_score=float(scores[index])) for index in ordered[:limit]]


def reciprocal_rank_fusion(
    ann_candidates: Sequence[RetrievalCandidate],
    bm25_candidates: Sequence[RetrievalCandidate],
    *,
    constant: int = 60,
    limit: int = 10,
) -> list[RetrievalCandidate]:
    """Merge two rankings by RRF without comparing incompatible raw scores."""
    if constant < 1 or limit < 1:
        raise ValueError("constant and limit must be at least 1")

    merged: dict[str, RetrievalCandidate] = {}
    rrf_scores: dict[str, float] = {}
    for candidates in (ann_candidates, bm25_candidates):
        for rank, candidate in enumerate(candidates, start=1):
            existing = merged.get(candidate.chunk_id)
            if existing is None:
                merged[candidate.chunk_id] = candidate
            else:
                merged[candidate.chunk_id] = replace(
                    existing,
                    ann_score=candidate.ann_score if candidate.ann_score is not None else existing.ann_score,
                    ann_distance=candidate.ann_distance if candidate.ann_distance is not None else existing.ann_distance,
                    bm25_score=candidate.bm25_score if candidate.bm25_score is not None else existing.bm25_score,
                )
            rrf_scores[candidate.chunk_id] = rrf_scores.get(candidate.chunk_id, 0.0) + 1.0 / (constant + rank)

    fused = [replace(candidate, rrf_score=rrf_scores[chunk_id]) for chunk_id, candidate in merged.items()]
    return sorted(fused, key=lambda candidate: candidate.rrf_score or 0.0, reverse=True)[:limit]


class HybridRetriever:
    """Retrieve Chroma ANN and BM25 candidates, then fuse and rerank them."""

    def __init__(
        self,
        config: RetrievalConfig | None = None,
        *,
        collection: ChromaCollection | None = None,
        embedder: SentenceTransformerEmbedder | None = None,
        reranker: CrossEncoderReranker | None = None,
    ) -> None:
        self.config = config or RetrievalConfig()
        self._collection = collection
        self._embedder = embedder
        self._reranker = reranker

    def _get_collection(self) -> ChromaCollection:
        if self._collection is None:
            try:
                import chromadb
            except ImportError as error:  # pragma: no cover - environment dependent
                raise RuntimeError(
                    "chromadb is required for retrieval. Install dependencies with: python3 -m pip install -r requirements.txt"
                ) from error
            client = chromadb.PersistentClient(path=str(self.config.database_path))
            self._collection = client.get_or_create_collection(
                name=self.config.collection_name, embedding_function=None
            )
        return self._collection

    def _get_embedder(self) -> SentenceTransformerEmbedder:
        if self._embedder is None:
            self._embedder = SentenceTransformerEmbedder(
                self.config.embedding_model_name,
                batch_size=self.config.embedding_batch_size,
                device=self.config.embedding_device,
            )
        return self._embedder

    def _get_reranker(self) -> CrossEncoderReranker:
        if self._reranker is None:
            self._reranker = CrossEncoderReranker(self.config.reranker)
        return self._reranker

    def _all_records(self) -> list[RetrievalCandidate]:
        response = self._get_collection().get(include=["documents", "metadatas"])
        ids = response.get("ids") or []
        documents = response.get("documents") or []
        metadatas = response.get("metadatas") or []
        return [
            _candidate_from_record(chunk_id, document or "", metadata)
            for chunk_id, document, metadata in zip(ids, documents, metadatas, strict=True)
        ]

    def ann_search(self, query: str, limit: int) -> list[RetrievalCandidate]:
        """Run ANN search using the exact embedding settings used for ingestion."""
        collection = self._get_collection()
        count = collection.count()
        if count == 0 or limit < 1:
            return []
        response = collection.query(
            query_embeddings=self._get_embedder().embed([query]),
            n_results=min(limit, count),
            include=["documents", "metadatas", "distances"],
        )
        ids = (response.get("ids") or [[]])[0]
        documents = (response.get("documents") or [[]])[0]
        metadatas = (response.get("metadatas") or [[]])[0]
        distances = (response.get("distances") or [[]])[0]
        return [
            _candidate_from_record(
                chunk_id, document or "", metadata,
                ann_distance=float(distance), ann_score=1.0 - float(distance),
            )
            for chunk_id, document, metadata, distance in zip(ids, documents, metadatas, distances, strict=True)
        ]

    def retrieve(self, query: str, k: int = 10) -> list[RetrievalCandidate]:
        """Run ANN + BM25 + RRF + cross-encoder reranking for one query."""
        if not query.strip():
            raise ValueError("query must not be empty")
        if k < 1:
            raise ValueError("k must be at least 1")
        collection = self._get_collection()
        if collection.count() == 0:
            LOGGER.info("Chroma collection %r is empty; no results returned", self.config.collection_name)
            return []
        ann_candidates = self.ann_search(query, self.config.ann_candidates)
        bm25_candidates = bm25_search(query, self._all_records(), self.config.bm25_candidates)
        fused = reciprocal_rank_fusion(
            ann_candidates, bm25_candidates,
            constant=self.config.rrf_constant, limit=self.config.hybrid_candidate_limit,
        )
        return self._get_reranker().rerank(query, fused)[:k]


def retrieve(query: str, k: int = 10) -> list[RetrievalCandidate]:
    """Retrieve ranked Kubernetes documentation chunks using default local settings."""
    return HybridRetriever().retrieve(query, k=k)


def _preview(text: str, length: int = 240) -> str:
    condensed = " ".join(text.split())
    return condensed if len(condensed) <= length else f"{condensed[:length].rstrip()}…"


def main() -> int:
    """Run the retrieval pipeline from the command line."""
    parser = argparse.ArgumentParser(description="Retrieve Kubernetes documentation chunks.")
    parser.add_argument("query")
    parser.add_argument("--k", type=int, default=10, help="Final results to return (default: 10)")
    parser.add_argument("--ann-candidates", type=int, default=RetrievalConfig.ann_candidates)
    parser.add_argument("--bm25-candidates", type=int, default=RetrievalConfig.bm25_candidates)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    config = RetrievalConfig(ann_candidates=args.ann_candidates, bm25_candidates=args.bm25_candidates)
    results = HybridRetriever(config).retrieve(args.query, k=args.k)
    if not results:
        print("No results. Run ingestion first or check the selected Chroma collection.")
        return 0
    for rank, result in enumerate(results, start=1):
        print(
            f"{rank}. source: {result.source_document}\n"
            f"   section: {result.section or 'n/a'}\n"
            f"   reranker score: {result.rerank_score:.4f}\n"
            f"   text: {_preview(result.text)}\n"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
