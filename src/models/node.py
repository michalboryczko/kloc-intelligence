"""Node data model for kloc-intelligence."""

from dataclasses import dataclass, field


@dataclass
class NodeData:
    """Represents a code symbol node from Neo4j."""

    node_id: str
    kind: str
    name: str
    fqn: str
    symbol: str
    file: str | None = None
    start_line: int | None = None
    start_col: int | None = None
    end_line: int | None = None
    end_col: int | None = None
    documentation: list[str] = field(default_factory=list)
    value_kind: str | None = None
    type_symbol: str | None = None
    call_kind: str | None = None
    signature: str | None = None
    enclosing_start_line: int | None = None
    enclosing_start_col: int | None = None
    enclosing_end_line: int | None = None
    enclosing_end_col: int | None = None

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
