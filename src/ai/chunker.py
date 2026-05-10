"""Code chunking for embedding large classes."""

import logging
from dataclasses import dataclass

from ..models.node import NodeData

logger = logging.getLogger(__name__)


@dataclass
class CodeChunk:
    """A chunk of source code ready for embedding."""

    node_id: str
    chunk_index: int
    total_chunks: int
    content: str

    @property
    def token_estimate(self) -> int:
        return len(self.content) // 4


class CodeChunker:
    """Splits large class bodies into embeddable chunks."""

    def __init__(self, max_tokens: int = 8000):
        self._max_tokens = max_tokens
        self._max_chars = max_tokens * 4  # rough chars-to-tokens ratio

    def chunk_node(
        self,
        node: NodeData,
        source: str,
        method_sources: list[tuple[NodeData, str]] | None = None,
    ) -> list[CodeChunk]:
        """Split node source into embeddable chunks.

        For Methods: always single chunk (truncate if too large).
        For Classes: single chunk if small enough, otherwise split by method
        boundaries with a class context prefix per chunk.
        """
        logger.debug(
            "Chunking %s %s: %d chars (~%d tokens), max=%d tokens",
            node.kind,
            node.fqn,
            len(source),
            len(source) // 4,
            self._max_tokens,
        )
        if node.kind == "Method" or node.kind == "Function":
            chunks = self._chunk_method(node, source)
            logger.debug("  -> %d chunk(s) for method", len(chunks))
            return chunks
        elif node.kind == "Class" or node.kind in ("Interface", "Trait", "Enum"):
            chunks = self._chunk_class(node, source, method_sources)
            logger.debug("  -> %d chunk(s) for class", len(chunks))
            return chunks
        else:
            logger.debug("  -> 1 chunk (fallback kind: %s)", node.kind)
            return [
                CodeChunk(
                    node_id=node.node_id,
                    chunk_index=0,
                    total_chunks=1,
                    content=self._truncate(source),
                )
            ]

    def _chunk_method(self, node: NodeData, source: str) -> list[CodeChunk]:
        """Methods are always a single chunk. Truncate if too large."""
        return [
            CodeChunk(
                node_id=node.node_id,
                chunk_index=0,
                total_chunks=1,
                content=self._truncate(source),
            )
        ]

    def _chunk_class(
        self,
        node: NodeData,
        source: str,
        method_sources: list[tuple[NodeData, str]] | None = None,
    ) -> list[CodeChunk]:
        """Chunk class by method boundaries if too large."""
        if len(source) <= self._max_chars:
            return [
                CodeChunk(
                    node_id=node.node_id,
                    chunk_index=0,
                    total_chunks=1,
                    content=source,
                )
            ]

        if not method_sources:
            # No method info available; truncate the whole class
            return [
                CodeChunk(
                    node_id=node.node_id,
                    chunk_index=0,
                    total_chunks=1,
                    content=self._truncate(source),
                )
            ]

        # Build class context prefix (declaration + properties, no method bodies)
        prefix = self._build_class_prefix(node, source, method_sources)
        prefix_len = len(prefix)
        available = self._max_chars - prefix_len - 100  # margin

        if available <= 0:
            # Prefix alone is too large
            return [
                CodeChunk(
                    node_id=node.node_id,
                    chunk_index=0,
                    total_chunks=1,
                    content=self._truncate(source),
                )
            ]

        # Pack methods into chunks
        chunks: list[CodeChunk] = []
        current_methods: list[str] = []
        current_len = 0

        for method_node, method_source in method_sources:
            method_len = len(method_source)
            if current_len + method_len > available and current_methods:
                # Flush current chunk
                chunk_content = prefix + "\n".join(current_methods)
                chunks.append(
                    CodeChunk(
                        node_id=node.node_id,
                        chunk_index=len(chunks),
                        total_chunks=0,  # will be set later
                        content=chunk_content,
                    )
                )
                current_methods = []
                current_len = 0
            current_methods.append(method_source)
            current_len += method_len

        # Flush remaining
        if current_methods:
            chunk_content = prefix + "\n".join(current_methods)
            chunks.append(
                CodeChunk(
                    node_id=node.node_id,
                    chunk_index=len(chunks),
                    total_chunks=0,
                    content=chunk_content,
                )
            )

        if not chunks:
            return [
                CodeChunk(
                    node_id=node.node_id,
                    chunk_index=0,
                    total_chunks=1,
                    content=self._truncate(source),
                )
            ]

        # Set total_chunks
        for chunk in chunks:
            chunk.total_chunks = len(chunks)

        return chunks

    def _build_class_prefix(
        self,
        node: NodeData,
        source: str,
        method_sources: list[tuple[NodeData, str]],
    ) -> str:
        """Build a class context prefix: declaration line + properties (no method bodies)."""
        lines = source.split("\n")
        prefix_lines = []
        # Take lines up to the first method body
        first_method_line = None
        for method_node, _ in method_sources:
            ml = method_node.enclosing_start_line or method_node.start_line
            if ml is not None:
                base = node.enclosing_start_line or node.start_line or 0
                relative = ml - base
                if first_method_line is None or relative < first_method_line:
                    first_method_line = relative

        if first_method_line is not None and first_method_line > 0:
            prefix_lines = lines[:first_method_line]
        else:
            # Fallback: take first 20 lines as header
            prefix_lines = lines[: min(20, len(lines))]

        prefix = "\n".join(prefix_lines)
        prefix += f"\n    // ... ({len(method_sources)} methods, chunked for embedding)\n\n"
        return prefix

    def _truncate(self, text: str) -> str:
        """Truncate text to max chars with a marker."""
        if len(text) <= self._max_chars:
            return text
        return text[: self._max_chars] + "\n// ... truncated ..."
