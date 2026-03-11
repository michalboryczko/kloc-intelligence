"""Node data model for kloc-intelligence."""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class NodeData:
    """Represents a code symbol node from Neo4j."""

    node_id: str
    kind: str
    name: str
    fqn: str
    symbol: str
    file: Optional[str] = None
    start_line: Optional[int] = None
    start_col: Optional[int] = None
    end_line: Optional[int] = None
    end_col: Optional[int] = None
    documentation: list[str] = field(default_factory=list)
    value_kind: Optional[str] = None
    type_symbol: Optional[str] = None
    call_kind: Optional[str] = None
    signature: Optional[str] = None
    enclosing_start_line: Optional[int] = None
    enclosing_start_col: Optional[int] = None
    enclosing_end_line: Optional[int] = None
    enclosing_end_col: Optional[int] = None

    @property
    def id(self) -> str:
        """Alias for node_id."""
        return self.node_id

    @property
    def location_str(self) -> str:
        """Human-readable location string."""
        if self.file and self.start_line is not None:
            return f"{self.file}:{self.start_line + 1}"
        elif self.file:
            return self.file
        return "<unknown>"

    @property
    def display_name(self) -> str:
        """Display name with signature for methods/functions."""
        if self.kind in ("Method", "Function") and self.signature:
            if "::" in self.fqn:
                class_part = self.fqn.rsplit("::", 1)[0]
                return f"{class_part}::{self.signature}"
            return self.signature
        return self.fqn
