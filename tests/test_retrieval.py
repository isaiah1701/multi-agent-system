"""Unit tests for hybrid retrieval without requiring a real Chroma database or models."""

from __future__ import annotations

import unittest
from dataclasses import replace
from typing import Any

from retrieval.retrieve import (
    CrossEncoderReranker,
    HybridRetriever,
    RetrievalCandidate,
    RetrievalConfig,
    bm25_search,
    reciprocal_rank_fusion,
)


def candidate(
    chunk_id: str,
    text: str | None = None,
    *,
    relative_path: str = "source.md",
    **scores: float,
) -> RetrievalCandidate:
    return RetrievalCandidate(
        chunk_id=chunk_id,
        text=text or f"text for {chunk_id}",
        source_document="source.md",
        section="Section",
        metadata={"relative_path": relative_path, "heading_path": "Section"},
        **scores,
    )


class FakeCollection:
    def __init__(self, records: list[RetrievalCandidate], ann_ids: list[str] | None = None) -> None:
        self.records = records
        self.ann_ids = ann_ids or [record.chunk_id for record in records]

    def count(self) -> int:
        return len(self.records)

    def get(self, *, include: list[str]) -> dict[str, Any]:
        return {
            "ids": [record.chunk_id for record in self.records],
            "documents": [record.text for record in self.records],
            "metadatas": [record.metadata for record in self.records],
        }

    def query(self, **kwargs: Any) -> dict[str, Any]:
        requested = {record.chunk_id: record for record in self.records}
        selected = [requested[chunk_id] for chunk_id in self.ann_ids[: kwargs["n_results"]]]
        return {
            "ids": [[record.chunk_id for record in selected]],
            "documents": [[record.text for record in selected]],
            "metadatas": [[record.metadata for record in selected]],
            "distances": [[0.1 + index for index, _ in enumerate(selected)]],
        }


class FakeEmbedder:
    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.25, 0.75] for _ in texts]


class IdentityReranker:
    def rerank(self, query: str, candidates: list[RetrievalCandidate]) -> list[RetrievalCandidate]:
        return [replace(item, rerank_score=float(len(candidates) - rank)) for rank, item in enumerate(candidates)]


class FakeCrossEncoder:
    def predict(self, pairs: list[tuple[str, str]], **kwargs: Any) -> list[float]:
        return [0.1 if "low" in passage else 0.9 for _, passage in pairs]


class RetrievalTests(unittest.TestCase):
    def test_bm25_retrieval_preserves_candidate_identity(self) -> None:
        results = bm25_search(
            "pod security admission",
            [candidate("one", "pod security admission rejects workloads"), candidate("two", "persistent volumes")],
            limit=1,
        )
        self.assertEqual([item.chunk_id for item in results], ["one"])
        self.assertIsNotNone(results[0].bm25_score)

    def test_ann_result_handling_preserves_distance_and_metadata(self) -> None:
        records = [candidate("one", "Pod Security Standards", relative_path="security.md")]
        retriever = HybridRetriever(collection=FakeCollection(records), embedder=FakeEmbedder())
        results = retriever.ann_search("pod security", limit=5)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].chunk_id, "one")
        self.assertEqual(results[0].ann_distance, 0.1)
        self.assertEqual(results[0].ann_score, 0.9)
        self.assertEqual(results[0].source_document, "security.md")

    def test_rrf_deduplicates_and_accumulates_both_scores(self) -> None:
        results = reciprocal_rank_fusion(
            [candidate("shared", ann_score=0.8), candidate("ann-only", ann_score=0.7)],
            [candidate("shared", bm25_score=2.0), candidate("bm25-only", bm25_score=1.0)],
            constant=10,
            limit=10,
        )
        self.assertEqual([item.chunk_id for item in results].count("shared"), 1)
        shared = next(item for item in results if item.chunk_id == "shared")
        self.assertEqual(shared.ann_score, 0.8)
        self.assertEqual(shared.bm25_score, 2.0)
        self.assertAlmostEqual(shared.rrf_score or 0, 2 / 11)

    def test_rrf_limit_keeps_top_ten_hybrid_candidates(self) -> None:
        results = reciprocal_rank_fusion(
            [candidate(f"ann-{index}") for index in range(12)],
            [candidate(f"bm25-{index}") for index in range(12)],
            limit=10,
        )
        self.assertEqual(len(results), 10)

    def test_reranker_orders_by_cross_encoder_score(self) -> None:
        reranker = CrossEncoderReranker()
        reranker._model = FakeCrossEncoder()  # Injected local model keeps this a unit test.
        results = reranker.rerank("query", [candidate("low", "low relevance"), candidate("high", "high relevance")])
        self.assertEqual([item.chunk_id for item in results], ["high", "low"])
        self.assertEqual(results[0].rerank_score, 0.9)

    def test_pipeline_limits_final_pool_to_ten_before_reranking(self) -> None:
        records = [candidate(f"chunk-{index}", f"pod security policy {index}") for index in range(14)]
        retriever = HybridRetriever(
            RetrievalConfig(ann_candidates=14, bm25_candidates=14, hybrid_candidate_limit=10),
            collection=FakeCollection(records),
            embedder=FakeEmbedder(),
            reranker=IdentityReranker(),
        )
        self.assertEqual(len(retriever.retrieve("pod security", k=10)), 10)

    def test_empty_corpus_returns_no_results_without_loading_models(self) -> None:
        retriever = HybridRetriever(collection=FakeCollection([]))
        self.assertEqual(retriever.retrieve("pod security"), [])


if __name__ == "__main__":
    unittest.main()
