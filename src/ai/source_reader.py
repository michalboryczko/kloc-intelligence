"""Read PHP source code from files using node metadata."""

import logging
from pathlib import Path

from ..models.node import NodeData

logger = logging.getLogger(__name__)


class SourceReader:
    """Reads source code for nodes using file path + line range from Neo4j."""

    def __init__(self, project_root: str):
        self._root = Path(project_root)
        logger.debug("SourceReader initialized with root: %s", project_root)

    def read_node_source(self, node: NodeData) -> str | None:
        """Read source code for a node using its file + enclosing range."""
        if not node.file:
            logger.debug("Node %s has no file field, skipping", node.fqn)
            return None

        file_path = self._root / node.file
        if not file_path.is_file():
            logger.debug("File not found: %s (node: %s)", file_path, node.fqn)
            return None

        # Prefer enclosing range (full body) over identifier range
        start = node.enclosing_start_line
        end = node.enclosing_end_line
        if start is None or end is None:
            start = node.start_line
            end = node.end_line
        if start is None or end is None:
            logger.debug("Node %s has no line range, skipping", node.fqn)
            return None

        source = self.read_file_range(str(file_path), start, end)
        if source:
            logger.debug(
                "Read source for %s: %s lines %d..%d (%d chars, ~%d tokens)",
                node.fqn,
                node.file,
                start,
                end,
                len(source),
                self.estimate_tokens(source),
            )
        return source

    def read_file_range(self, file_path: str, start_line: int, end_line: int) -> str | None:
        """Read a specific line range from a file (0-based line numbers)."""
        try:
            with open(file_path, encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except OSError as e:
            logger.debug("Failed to read %s: %s", file_path, e)
            return None

        if start_line < 0:
            start_line = 0
        if end_line >= len(lines):
            end_line = len(lines) - 1

        if start_line > end_line or start_line >= len(lines):
            return None

        return "".join(lines[start_line : end_line + 1])

    @staticmethod
    def estimate_tokens(text: str) -> int:
        """Rough token estimate (chars / 4 as approximation)."""
        return len(text) // 4
