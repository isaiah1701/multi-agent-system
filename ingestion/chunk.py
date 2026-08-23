"""Document-aware semantic chunking for the Kubernetes Markdown corpus.

The module parses Markdown into logical heading sections and content blocks
before using local embeddings to choose boundaries within sections that exceed
the configured size limit. Code fences are treated as atomic blocks.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, Sequence

import yaml


LOGGER = logging.getLogger(__name__)
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CORPUS_PATH = REPOSITORY_ROOT / "corpus" / "kubernetes"

HEADING_PATTERN = re.compile(r"^(?P<markers>#{1,6})\s+(?P<title>.+?)\s*$")
FENCE_PATTERN = re.compile(r"^\s*(?P<fence>`{3,}|~{3,})")
LIST_ITEM_PATTERN = re.compile(r"^\s*(?:[-+*]|\d+[.)])\s+")
SENTENCE_BOUNDARY_PATTERN = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9`])")
HTML_COMMENT_PATTERN = re.compile(r"<!--.*?-->", re.DOTALL)
HEADING_ANCHOR_PATTERN = re.compile(r"\s*\{#[^}]+\}\s*$")
SHORTCODE_PATTERN = re.compile(r"\{\{[%<].*?[%>]\}\}")


class EmbeddingProvider(Protocol):
    """Small interface used by semantic chunking and ingestion."""

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Return one embedding vector for each input text."""


@dataclass(frozen=True)
class ChunkingConfig:
    """Configuration for document parsing and semantic boundary selection."""

    embedding_model_name: str = "sentence-transformers/all-MiniLM-L6-v2"
    embedding_device: str | None = None
    embedding_batch_size: int = 32
    min_chunk_words: int = 80
    max_chunk_words: int = 350
    semantic_breakpoint_percentile: float = 20.0

    def __post_init__(self) -> None:
        if self.min_chunk_words < 1:
            raise ValueError("min_chunk_words must be at least 1")
        if self.max_chunk_words < self.min_chunk_words:
            raise ValueError("max_chunk_words must be >= min_chunk_words")
        if not 0 <= self.semantic_breakpoint_percentile <= 100:
            raise ValueError("semantic_breakpoint_percentile must be between 0 and 100")
        if self.embedding_batch_size < 1:
            raise ValueError("embedding_batch_size must be at least 1")


@dataclass(frozen=True)
class ContentBlock:
    """An indivisible Markdown paragraph, list item, or fenced code block."""

    text: str
    kind: str

    @property
    def word_count(self) -> int:
        return word_count(self.text)


@dataclass(frozen=True)
class LogicalSection:
    """Content below a single heading, retaining its active heading hierarchy."""

    heading_path: tuple[str, ...]
    blocks: tuple[ContentBlock, ...]

    @property
    def text(self) -> str:
        return "\n\n".join(block.text for block in self.blocks).strip()

    @property
    def word_count(self) -> int:
        return word_count(self.text)


@dataclass(frozen=True)
class Chunk:
    """A structured, stable unit of source documentation for later retrieval."""

    chunk_id: str
    content: str
    source_document: str
    relative_path: str
    document_title: str
    heading_path: tuple[str, ...]
    corpus_hierarchy: tuple[str, ...]
    metadata: dict[str, Any]

    @property
    def context_path(self) -> tuple[str, ...]:
        """The corpus and document heading context, without duplicate titles."""
        suffix = self.heading_path[1:] if self.heading_path else ()
        return (*self.corpus_hierarchy, *suffix)

    @property
    def embedding_text(self) -> str:
        """Contextual text used only when calculating the chunk embedding."""
        section = " > ".join(self.context_path)
        return f"Document: {self.document_title}\nSection: {section}\n\n{self.content}"

    def vector_metadata(self) -> dict[str, str | int | float | bool]:
        """Return Chroma-compatible metadata while retaining source context."""
        vector_metadata: dict[str, str | int | float | bool] = {
            "source_document": self.source_document,
            "relative_path": self.relative_path,
            "document_title": self.document_title,
            "heading_path": " > ".join(self.heading_path),
            "corpus_hierarchy": " > ".join(self.corpus_hierarchy),
            "context_path": " > ".join(self.context_path),
        }
        for key, value in self.metadata.items():
            if value is None:
                continue
            if isinstance(value, (str, int, float, bool)):
                vector_metadata[key] = value
            else:
                vector_metadata[key] = json.dumps(value, ensure_ascii=False, sort_keys=True)
        return vector_metadata

    def to_dict(self, preview_characters: int | None = None) -> dict[str, Any]:
        """Return a serialisable representation suitable for inspection/JSONL."""
        content = self.content
        if preview_characters is not None and len(content) > preview_characters:
            content = f"{content[:preview_characters].rstrip()}…"
        return {
            "chunk_id": self.chunk_id,
            "content": content,
            "source_document": self.source_document,
            "relative_path": self.relative_path,
            "document_title": self.document_title,
            "heading_path": list(self.heading_path),
            "corpus_hierarchy": list(self.corpus_hierarchy),
            "context_path": list(self.context_path),
            "metadata": self.metadata,
        }


@dataclass
class ChunkingResult:
    """Chunks and non-fatal failures from a corpus-chunking run."""

    chunks: list[Chunk] = field(default_factory=list)
    documents_discovered: int = 0
    documents_processed: int = 0
    failures: list[str] = field(default_factory=list)

    @property
    def statistics(self) -> dict[str, int]:
        return {
            "documents_discovered": self.documents_discovered,
            "documents_processed": self.documents_processed,
            "chunks_generated": len(self.chunks),
            "failed_or_skipped_documents": len(self.failures),
        }


class SentenceTransformerEmbedder:
    """Lazy local embedding provider shared by semantic chunking and ingestion."""

    def __init__(
        self,
        model_name: str,
        *,
        batch_size: int = 32,
        device: str | None = None,
    ) -> None:
        self.model_name = model_name
        self.batch_size = batch_size
        self.device = device
        self._model: Any | None = None

    def _load_model(self) -> Any:
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as error:  # pragma: no cover - depends on environment
                raise RuntimeError(
                    "sentence-transformers is required for semantic chunking. "
                    "Install dependencies with: python3 -m pip install -r requirements.txt"
                ) from error
            LOGGER.info("Loading local embedding model: %s", self.model_name)
            self._model = SentenceTransformer(self.model_name, device=self.device)
        return self._model

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors = self._load_model().encode(
            list(texts),
            batch_size=self.batch_size,
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return vectors.tolist()


def word_count(text: str) -> int:
    """Return a lightweight, tokenizer-independent size estimate."""
    return len(re.findall(r"\S+", text))


def _strip_front_matter(markdown: str) -> tuple[dict[str, Any], str]:
    """Read optional YAML front matter without treating it as document content."""
    if not markdown.startswith("---"):
        return {}, markdown

    lines = markdown.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return {}, markdown
    for position, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            raw_front_matter = "".join(lines[1:position])
            parsed = yaml.safe_load(raw_front_matter) or {}
            if not isinstance(parsed, dict):
                raise ValueError("YAML front matter must be a mapping")
            return parsed, "".join(lines[position + 1 :])
    return {}, markdown


def _normalise_heading(heading: str) -> str:
    heading = HEADING_ANCHOR_PATTERN.sub("", heading).strip()
    return re.sub(r"\s+", " ", heading)


def _clean_markdown(markdown: str) -> str:
    """Remove non-content comments and standalone Hugo shortcode markup."""
    without_comments = HTML_COMMENT_PATTERN.sub("", markdown)
    return SHORTCODE_PATTERN.sub("", without_comments)


def _split_text_blocks(lines: Sequence[str]) -> list[ContentBlock]:
    """Split ordinary Markdown text into paragraphs and complete list items."""
    groups: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if not line.strip():
            if current:
                groups.append(current)
                current = []
            continue
        current.append(line)
    if current:
        groups.append(current)

    blocks: list[ContentBlock] = []
    for group in groups:
        if LIST_ITEM_PATTERN.match(group[0]):
            item: list[str] = []
            for line in group:
                if LIST_ITEM_PATTERN.match(line) and item:
                    blocks.append(ContentBlock("\n".join(item).strip(), "list_item"))
                    item = [line]
                else:
                    item.append(line)
            if item:
                blocks.append(ContentBlock("\n".join(item).strip(), "list_item"))
        else:
            blocks.append(ContentBlock("\n".join(group).strip(), "paragraph"))
    return blocks


def _blocks_from_lines(lines: Sequence[str]) -> tuple[ContentBlock, ...]:
    """Create blocks while keeping every fenced code block indivisible."""
    blocks: list[ContentBlock] = []
    text_lines: list[str] = []
    code_lines: list[str] | None = None
    closing_fence: str | None = None

    def flush_text() -> None:
        nonlocal text_lines
        if text_lines:
            blocks.extend(_split_text_blocks(text_lines))
            text_lines = []

    for line in lines:
        fence_match = FENCE_PATTERN.match(line)
        if code_lines is None and fence_match:
            flush_text()
            code_lines = [line]
            closing_fence = fence_match.group("fence")[0]
            continue
        if code_lines is not None:
            code_lines.append(line)
            if fence_match and fence_match.group("fence")[0] == closing_fence:
                blocks.append(ContentBlock("\n".join(code_lines).strip(), "code"))
                code_lines = None
                closing_fence = None
            continue
        text_lines.append(line)

    if code_lines is not None:
        # Keep malformed, unclosed fences together too; callers may still index it.
        blocks.append(ContentBlock("\n".join(code_lines).strip(), "code"))
    flush_text()
    return tuple(block for block in blocks if block.text)


def parse_markdown_sections(markdown: str, document_title: str) -> list[LogicalSection]:
    """Parse headings outside code fences into sections with complete blocks."""
    _, body = _strip_front_matter(markdown)
    lines = _clean_markdown(body).splitlines()
    sections: list[LogicalSection] = []
    active_headings: list[tuple[int, str]] = []
    section_lines: list[str] = []
    inside_fence = False
    fence_character: str | None = None

    def flush_section() -> None:
        nonlocal section_lines
        blocks = _blocks_from_lines(section_lines)
        if blocks:
            headings = tuple(title for _, title in active_headings)
            sections.append(LogicalSection(headings, blocks))
        section_lines = []

    for line in lines:
        fence_match = FENCE_PATTERN.match(line)
        if fence_match:
            marker = fence_match.group("fence")[0]
            if not inside_fence:
                inside_fence = True
                fence_character = marker
            elif marker == fence_character:
                inside_fence = False
                fence_character = None
            section_lines.append(line)
            continue

        heading_match = None if inside_fence else HEADING_PATTERN.match(line)
        if heading_match:
            flush_section()
            heading_level = len(heading_match.group("markers"))
            heading_title = _normalise_heading(heading_match.group("title"))
            active_headings = [
                (level, title) for level, title in active_headings if level < heading_level
            ]
            active_headings.append((heading_level, heading_title))
            continue
        section_lines.append(line)
    flush_section()

    # Documents without headings still retain their title as context downstream.
    return sections


def _title_from_markdown(path: Path) -> str | None:
    try:
        metadata, body = _strip_front_matter(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return None
    title = metadata.get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()
    for line in body.splitlines():
        match = HEADING_PATTERN.match(line)
        if match:
            return _normalise_heading(match.group("title"))
    return None


def _corpus_hierarchy(
    source_path: Path,
    corpus_path: Path,
    document_title: str,
    title_cache: dict[Path, str | None],
) -> tuple[str, ...]:
    """Derive parent Concepts titles from the corpus' `_index.md` files."""
    relative_parent = source_path.relative_to(corpus_path).parent
    directories = list(relative_parent.parts)
    if source_path.name == "_index.md" and directories:
        directories.pop()

    hierarchy = ["Kubernetes"]
    current = corpus_path
    for directory in directories:
        current = current / directory
        index_path = current / "_index.md"
        if index_path not in title_cache:
            title_cache[index_path] = _title_from_markdown(index_path) if index_path.exists() else None
        hierarchy.append(title_cache[index_path] or directory.replace("-", " ").title())
    hierarchy.append(document_title)
    return tuple(hierarchy)


def _section_heading_path(document_title: str, headings: tuple[str, ...]) -> tuple[str, ...]:
    """Prefix section headings with the document title without duplicating an H1."""
    if headings and headings[0].casefold() == document_title.casefold():
        headings = headings[1:]
    return (document_title, *headings)


def _semantic_units(section: LogicalSection) -> list[ContentBlock]:
    """Use sentences/list items as semantic units but leave code blocks untouched."""
    units: list[ContentBlock] = []
    for block in section.blocks:
        if block.kind == "code":
            units.append(block)
            continue
        if block.kind == "list_item":
            units.append(block)
            continue
        sentences = SENTENCE_BOUNDARY_PATTERN.split(block.text)
        units.extend(
            ContentBlock(sentence.strip(), "sentence")
            for sentence in sentences
            if sentence.strip()
        )
    return units


def _cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    numerator = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if not left_norm or not right_norm:
        return 0.0
    return numerator / (left_norm * right_norm)


def _percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile / 100
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _merge_short_chunks(
    groups: list[list[ContentBlock]],
    reasons: list[str],
    config: ChunkingConfig,
) -> tuple[list[list[ContentBlock]], list[str]]:
    """Merge short neighbours when doing so respects the maximum size guardrail."""
    position = 1
    while position < len(groups):
        previous = groups[position - 1]
        current = groups[position]
        combined_words = sum(block.word_count for block in previous + current)
        if (
            sum(block.word_count for block in current) < config.min_chunk_words
            and combined_words <= config.max_chunk_words
        ):
            previous.extend(current)
            groups.pop(position)
            reasons.pop(position)
        else:
            position += 1
    return groups, reasons


def _semantic_split_section(
    section: LogicalSection,
    embedder: EmbeddingProvider,
    config: ChunkingConfig,
) -> list[tuple[str, dict[str, Any]]]:
    """Split an oversized section using embedding similarity plus size guardrails.

    A similarity percentile is calculated from adjacent semantic units. A low
    similarity can form a boundary after the minimum chunk size is reached;
    maximum size remains a safety limit. If embeddings cannot be generated, the
    error is propagated rather than silently reverting to fixed-size chunking.
    """
    units = _semantic_units(section)
    if not units:
        return []
    vectors = embedder.embed([unit.text for unit in units])
    if len(vectors) != len(units):
        raise RuntimeError("Embedding provider returned an unexpected number of vectors")
    similarities = [
        _cosine_similarity(vectors[index - 1], vectors[index])
        for index in range(1, len(vectors))
    ]
    semantic_cutoff = _percentile(similarities, config.semantic_breakpoint_percentile)

    groups: list[list[ContentBlock]] = []
    reasons: list[str] = []
    current: list[ContentBlock] = []
    current_words = 0
    for index, unit in enumerate(units):
        unit_words = unit.word_count
        similarity_before = similarities[index - 1] if index else None
        should_break_semantically = (
            bool(current)
            and current_words >= config.min_chunk_words
            and similarity_before is not None
            and similarity_before <= semantic_cutoff
        )
        exceeds_maximum = bool(current) and current_words + unit_words > config.max_chunk_words
        if should_break_semantically or exceeds_maximum:
            groups.append(current)
            reasons.append("semantic" if should_break_semantically else "max_size_guardrail")
            current = []
            current_words = 0
        current.append(unit)
        current_words += unit_words
    if current:
        groups.append(current)
        reasons.append("section_start")

    groups, reasons = _merge_short_chunks(groups, reasons, config)
    chunks: list[tuple[str, dict[str, Any]]] = []
    for group, boundary_reason in zip(groups, reasons, strict=True):
        content = "\n\n".join(block.text for block in group).strip()
        chunks.append(
            (
                content,
                {
                    "chunking_strategy": "semantic",
                    "boundary_reason": boundary_reason,
                    "semantic_similarity_cutoff": round(semantic_cutoff, 6),
                    "contains_code_block": any(block.kind == "code" for block in group),
                    "oversized_atomic_block": any(
                        block.kind == "code" and block.word_count > config.max_chunk_words
                        for block in group
                    ),
                },
            )
        )
    return chunks


def _chunk_identifier(relative_path: str, heading_path: tuple[str, ...], content: str) -> str:
    stable_input = "\0".join((relative_path, " > ".join(heading_path), content))
    return f"k8s-{hashlib.sha256(stable_input.encode('utf-8')).hexdigest()[:24]}"


def chunk_markdown_file(
    source_path: Path,
    corpus_path: Path,
    embedder: EmbeddingProvider,
    config: ChunkingConfig,
    title_cache: dict[Path, str | None] | None = None,
) -> list[Chunk]:
    """Parse and semantically chunk one Markdown document."""
    markdown = source_path.read_text(encoding="utf-8")
    document_metadata, _ = _strip_front_matter(markdown)
    document_title = document_metadata.get("title")
    if not isinstance(document_title, str) or not document_title.strip():
        document_title = _title_from_markdown(source_path) or source_path.stem.replace("-", " ").title()
    document_title = document_title.strip()
    relative_path = source_path.relative_to(corpus_path).as_posix()
    hierarchy = _corpus_hierarchy(
        source_path,
        corpus_path,
        document_title,
        title_cache if title_cache is not None else {},
    )

    chunks: list[Chunk] = []
    for section in parse_markdown_sections(markdown, document_title):
        heading_path = _section_heading_path(document_title, section.heading_path)
        if section.word_count <= config.max_chunk_words:
            pieces = [
                (
                    section.text,
                    {
                        "chunking_strategy": "logical_section",
                        "boundary_reason": "logical_section",
                        "contains_code_block": any(block.kind == "code" for block in section.blocks),
                        "oversized_atomic_block": False,
                    },
                )
            ]
        else:
            pieces = _semantic_split_section(section, embedder, config)

        for content, chunk_metadata in pieces:
            metadata: dict[str, Any] = {
                "content_type": document_metadata.get("content_type"),
                "document_weight": document_metadata.get("weight"),
                "document_metadata": document_metadata,
                "word_count": word_count(content),
                **chunk_metadata,
            }
            chunks.append(
                Chunk(
                    chunk_id=_chunk_identifier(relative_path, heading_path, content),
                    content=content,
                    source_document=source_path.name,
                    relative_path=relative_path,
                    document_title=document_title,
                    heading_path=heading_path,
                    corpus_hierarchy=hierarchy,
                    metadata=metadata,
                )
            )
    return chunks


def discover_markdown_documents(corpus_path: Path, limit: int | None = None) -> list[Path]:
    """Return Markdown documents in deterministic relative-path order."""
    documents = sorted(corpus_path.rglob("*.md"), key=lambda path: path.as_posix())
    return documents[:limit] if limit is not None else documents


def chunk_corpus(
    corpus_path: Path,
    *,
    config: ChunkingConfig | None = None,
    embedder: EmbeddingProvider | None = None,
    limit: int | None = None,
) -> ChunkingResult:
    """Chunk a corpus, retaining failures so malformed documents do not stop a run."""
    corpus_path = corpus_path.resolve()
    if not corpus_path.is_dir():
        raise FileNotFoundError(f"Corpus directory does not exist: {corpus_path}")
    if limit is not None and limit < 1:
        raise ValueError("limit must be at least 1 when provided")

    chunking_config = config or ChunkingConfig()
    semantic_embedder = embedder or SentenceTransformerEmbedder(
        chunking_config.embedding_model_name,
        batch_size=chunking_config.embedding_batch_size,
        device=chunking_config.embedding_device,
    )
    documents = discover_markdown_documents(corpus_path, limit)
    result = ChunkingResult(documents_discovered=len(documents))
    title_cache: dict[Path, str | None] = {}
    for document in documents:
        try:
            document_chunks = chunk_markdown_file(
                document,
                corpus_path,
                semantic_embedder,
                chunking_config,
                title_cache,
            )
        except (OSError, UnicodeDecodeError, ValueError, yaml.YAMLError, RuntimeError) as error:
            message = f"{document.relative_to(corpus_path)}: {error}"
            LOGGER.warning("Skipping malformed document: %s", message)
            result.failures.append(message)
            continue
        result.chunks.extend(document_chunks)
        result.documents_processed += 1
    return result


def write_chunks_jsonl(chunks: Sequence[Chunk], output_path: Path) -> None:
    """Write full structured chunks to JSONL for standalone inspection."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as output_file:
        for chunk in chunks:
            output_file.write(json.dumps(chunk.to_dict(), ensure_ascii=False) + "\n")


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Parse Kubernetes Markdown into document-aware semantic chunks.",
    )
    parser.add_argument(
        "--corpus",
        type=Path,
        default=DEFAULT_CORPUS_PATH,
        help="Path to the Kubernetes corpus (default: %(default)s)",
    )
    parser.add_argument("--limit", type=int, help="Process only the first N documents")
    parser.add_argument("--output", type=Path, help="Optional JSONL file for complete chunk output")
    parser.add_argument("--show", type=int, default=3, help="Number of representative chunks to print")
    parser.add_argument(
        "--model",
        default=ChunkingConfig.embedding_model_name,
        help="Local SentenceTransformers model used for semantic boundaries",
    )
    parser.add_argument("--device", help="Optional SentenceTransformers device, for example cpu")
    parser.add_argument("--embedding-batch-size", type=int, default=ChunkingConfig.embedding_batch_size)
    parser.add_argument("--min-chunk-words", type=int, default=ChunkingConfig.min_chunk_words)
    parser.add_argument("--max-chunk-words", type=int, default=ChunkingConfig.max_chunk_words)
    parser.add_argument(
        "--semantic-breakpoint-percentile",
        type=float,
        default=ChunkingConfig.semantic_breakpoint_percentile,
        help="Percentile of adjacent semantic similarity used as a possible boundary",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    return parser


def main() -> int:
    """Run standalone chunk inspection from the repository root."""
    args = _argument_parser().parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    config = ChunkingConfig(
        embedding_model_name=args.model,
        embedding_device=args.device,
        embedding_batch_size=args.embedding_batch_size,
        min_chunk_words=args.min_chunk_words,
        max_chunk_words=args.max_chunk_words,
        semantic_breakpoint_percentile=args.semantic_breakpoint_percentile,
    )
    result = chunk_corpus(args.corpus, config=config, limit=args.limit)
    if args.output:
        write_chunks_jsonl(result.chunks, args.output)
        LOGGER.info("Wrote %s chunks to %s", len(result.chunks), args.output)
    summary: dict[str, Any] = {**result.statistics, "failures": result.failures}
    print(json.dumps(summary, indent=2))
    for chunk in result.chunks[: args.show]:
        print(json.dumps(chunk.to_dict(preview_characters=700), ensure_ascii=False, indent=2))
    return 0 if not result.failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
