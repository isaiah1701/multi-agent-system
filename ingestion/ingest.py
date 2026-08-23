"""Embed and persist document-aware Kubernetes chunks in a local Chroma store."""

from __future__ import annotations

import argparse
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

try:  # Supports both `python -m ingestion.ingest` and direct script execution.
    from ingestion.chunk import (
        DEFAULT_CORPUS_PATH,
        Chunk,
        ChunkingConfig,
        SentenceTransformerEmbedder,
        chunk_corpus,
    )
except ModuleNotFoundError:  # pragma: no cover - direct-script convenience path
    from chunk import (  # type: ignore[no-redef]
        DEFAULT_CORPUS_PATH,
        Chunk,
        ChunkingConfig,
        SentenceTransformerEmbedder,
        chunk_corpus,
    )


LOGGER = logging.getLogger(__name__)
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE_PATH = REPOSITORY_ROOT / "data" / "chroma"
DEFAULT_COLLECTION_NAME = "kubernetes_concepts"


@dataclass(frozen=True)
class IngestionConfig:
    """Runtime settings for local embedding and Chroma persistence."""

    corpus_path: Path = DEFAULT_CORPUS_PATH
    database_path: Path = DEFAULT_DATABASE_PATH
    collection_name: str = DEFAULT_COLLECTION_NAME
    embedding_model_name: str = ChunkingConfig.embedding_model_name
    embedding_device: str | None = None
    embedding_batch_size: int = 32
    min_chunk_words: int = ChunkingConfig.min_chunk_words
    max_chunk_words: int = ChunkingConfig.max_chunk_words
    semantic_breakpoint_percentile: float = ChunkingConfig.semantic_breakpoint_percentile
    document_limit: int | None = None

    def chunking_config(self) -> ChunkingConfig:
        """Create the configuration shared with the chunking layer."""
        return ChunkingConfig(
            embedding_model_name=self.embedding_model_name,
            embedding_device=self.embedding_device,
            embedding_batch_size=self.embedding_batch_size,
            min_chunk_words=self.min_chunk_words,
            max_chunk_words=self.max_chunk_words,
            semantic_breakpoint_percentile=self.semantic_breakpoint_percentile,
        )


@dataclass
class IngestionResult:
    """Counts and failures from one ingestion run."""

    documents_discovered: int = 0
    documents_processed: int = 0
    chunks_generated: int = 0
    chunks_embedded: int = 0
    chunks_stored: int = 0
    collection_records: int = 0
    failures: list[str] = field(default_factory=list)
    elapsed_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "documents_discovered": self.documents_discovered,
            "documents_processed": self.documents_processed,
            "chunks_generated": self.chunks_generated,
            "chunks_embedded": self.chunks_embedded,
            "chunks_stored": self.chunks_stored,
            "collection_records": self.collection_records,
            "failures_or_skipped_documents": len(self.failures),
            "failures": self.failures,
            "total_ingestion_seconds": round(self.elapsed_seconds, 3),
        }


@dataclass(frozen=True)
class EmbeddedChunk:
    """A chunk and its locally generated vector, ready for vector storage."""

    chunk: Chunk
    embedding: list[float]


def _batched(values: Sequence[Chunk], batch_size: int) -> Iterable[Sequence[Chunk]]:
    for start in range(0, len(values), batch_size):
        yield values[start : start + batch_size]


def _embed_chunks(
    chunks: Sequence[Chunk],
    embedder: SentenceTransformerEmbedder,
    batch_size: int,
) -> tuple[list[EmbeddedChunk], set[str], list[str]]:
    """Embed chunks in batches, isolating a bad document if a batch fails.

    Documents with any failed vector are excluded as a whole. This prevents a
    later re-ingestion from mixing fresh chunks with stale chunks from the same
    source document.
    """
    embedded: list[EmbeddedChunk] = []
    failed_documents: set[str] = set()
    failures: list[str] = []
    for batch in _batched(chunks, batch_size):
        try:
            vectors = embedder.embed([chunk.embedding_text for chunk in batch])
            if len(vectors) != len(batch):
                raise RuntimeError("Embedding provider returned an unexpected number of vectors")
            embedded.extend(
                EmbeddedChunk(chunk=chunk, embedding=vector)
                for chunk, vector in zip(batch, vectors, strict=True)
            )
        except Exception as batch_error:  # Individual retries retain useful documents.
            LOGGER.warning("Embedding batch failed; retrying individual chunks: %s", batch_error)
            for chunk in batch:
                try:
                    vector = embedder.embed([chunk.embedding_text])[0]
                    embedded.append(EmbeddedChunk(chunk=chunk, embedding=vector))
                except Exception as error:
                    failed_documents.add(chunk.relative_path)
                    message = f"{chunk.relative_path}: embedding failed: {error}"
                    LOGGER.warning(message)
                    failures.append(message)

    if failed_documents:
        embedded = [
            record for record in embedded if record.chunk.relative_path not in failed_documents
        ]
    return embedded, failed_documents, failures


def _get_collection(database_path: Path, collection_name: str) -> Any:
    """Open a persistent local Chroma collection without an implicit embedder."""
    try:
        import chromadb
    except ImportError as error:  # pragma: no cover - depends on environment
        raise RuntimeError(
            "chromadb is required for ingestion. "
            "Install dependencies with: python3 -m pip install -r requirements.txt"
        ) from error

    database_path.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(database_path))
    return client.get_or_create_collection(
        name=collection_name,
        metadata={"hnsw:space": "cosine", "description": "Kubernetes Concepts documentation"},
        embedding_function=None,
    )


def _delete_existing_documents(collection: Any, relative_paths: Sequence[str]) -> None:
    """Delete prior vectors for successfully re-embedded source documents."""
    if not relative_paths:
        return
    # Chroma supports `$in`; batches keep metadata queries manageable for a full corpus.
    metadata_batch_size = 100
    for start in range(0, len(relative_paths), metadata_batch_size):
        paths = list(relative_paths[start : start + metadata_batch_size])
        collection.delete(where={"relative_path": {"$in": paths}})


def _upsert_embedded_chunks(
    collection: Any,
    embedded_chunks: Sequence[EmbeddedChunk],
    batch_size: int,
) -> int:
    """Upsert vectors, raw chunk content, stable IDs, and flattened metadata."""
    stored = 0
    for start in range(0, len(embedded_chunks), batch_size):
        batch = embedded_chunks[start : start + batch_size]
        collection.upsert(
            ids=[record.chunk.chunk_id for record in batch],
            embeddings=[record.embedding for record in batch],
            documents=[record.chunk.content for record in batch],
            metadatas=[record.chunk.vector_metadata() for record in batch],
        )
        stored += len(batch)
    return stored


def ingest_corpus(config: IngestionConfig) -> IngestionResult:
    """Chunk, embed, and persist the Kubernetes corpus with safe re-runs.

    Re-ingestion replaces all records for documents that completed embedding.
    Unchanged chunks also have deterministic IDs, so upserts are idempotent.
    Documents that fail parsing or embedding leave their previous records intact.
    """
    started_at = time.perf_counter()
    chunking_config = config.chunking_config()
    embedder = SentenceTransformerEmbedder(
        config.embedding_model_name,
        batch_size=config.embedding_batch_size,
        device=config.embedding_device,
    )
    chunking_result = chunk_corpus(
        config.corpus_path,
        config=chunking_config,
        embedder=embedder,
        limit=config.document_limit,
    )
    result = IngestionResult(
        documents_discovered=chunking_result.documents_discovered,
        documents_processed=chunking_result.documents_processed,
        chunks_generated=len(chunking_result.chunks),
        failures=list(chunking_result.failures),
    )
    if not chunking_result.chunks:
        result.elapsed_seconds = time.perf_counter() - started_at
        LOGGER.warning("No chunks were generated; vector store was not modified")
        return result

    embedded_chunks, failed_embedding_documents, embedding_failures = _embed_chunks(
        chunking_result.chunks,
        embedder,
        config.embedding_batch_size,
    )
    result.chunks_embedded = len(embedded_chunks)
    result.failures.extend(embedding_failures)
    if not embedded_chunks:
        result.elapsed_seconds = time.perf_counter() - started_at
        LOGGER.warning("No chunks were embedded; vector store was not modified")
        return result

    collection = _get_collection(config.database_path, config.collection_name)
    successful_paths = sorted(
        {
            record.chunk.relative_path
            for record in embedded_chunks
            if record.chunk.relative_path not in failed_embedding_documents
        }
    )
    _delete_existing_documents(collection, successful_paths)
    result.chunks_stored = _upsert_embedded_chunks(
        collection,
        embedded_chunks,
        config.embedding_batch_size,
    )
    result.collection_records = collection.count()
    result.elapsed_seconds = time.perf_counter() - started_at
    LOGGER.info(
        "Ingestion complete: %d documents, %d chunks stored, %d collection records",
        result.documents_processed,
        result.chunks_stored,
        result.collection_records,
    )
    return result


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Embed Kubernetes documentation chunks into a persistent local Chroma database.",
    )
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS_PATH)
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DATABASE_PATH)
    parser.add_argument("--collection", default=DEFAULT_COLLECTION_NAME)
    parser.add_argument("--limit", type=int, help="Process only the first N Markdown documents")
    parser.add_argument(
        "--model",
        default=ChunkingConfig.embedding_model_name,
        help="Local SentenceTransformers model used for chunking and embedding",
    )
    parser.add_argument("--device", help="Optional SentenceTransformers device, for example cpu")
    parser.add_argument("--embedding-batch-size", type=int, default=32)
    parser.add_argument("--min-chunk-words", type=int, default=ChunkingConfig.min_chunk_words)
    parser.add_argument("--max-chunk-words", type=int, default=ChunkingConfig.max_chunk_words)
    parser.add_argument(
        "--semantic-breakpoint-percentile",
        type=float,
        default=ChunkingConfig.semantic_breakpoint_percentile,
    )
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    return parser


def main() -> int:
    """Run a local ingestion job from the repository root."""
    args = _argument_parser().parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    config = IngestionConfig(
        corpus_path=args.corpus,
        database_path=args.db_path,
        collection_name=args.collection,
        embedding_model_name=args.model,
        embedding_device=args.device,
        embedding_batch_size=args.embedding_batch_size,
        min_chunk_words=args.min_chunk_words,
        max_chunk_words=args.max_chunk_words,
        semantic_breakpoint_percentile=args.semantic_breakpoint_percentile,
        document_limit=args.limit,
    )
    result = ingest_corpus(config)
    print(json.dumps(result.to_dict(), indent=2))
    return 0 if not result.failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
