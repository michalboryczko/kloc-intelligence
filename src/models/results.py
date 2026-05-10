"""Result models for usages, deps, owners, inherit, overrides, and context commands."""

from dataclasses import dataclass, field
from typing import Optional

from .node import NodeData


@dataclass
class UsageEntry:
    """Single usage entry with tree support."""

    depth: int
    node_id: str
    fqn: str
    file: str | None = None
    line: int | None = None
    children: list["UsageEntry"] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = {"depth": self.depth, "node_id": self.node_id, "fqn": self.fqn}
        if self.file is not None:
            d["file"] = self.file
        if self.line is not None:
            d["line"] = self.line + 1  # 0-based to 1-based for output
        if self.children:
            d["children"] = [c.to_dict() for c in self.children]
        return d


@dataclass
class UsagesTreeResult:
    """Result of usages query with tree structure."""

    target: NodeData
    max_depth: int
    tree: list[UsageEntry] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "target": {
                "id": self.target.node_id,
                "kind": self.target.kind,
                "fqn": self.target.fqn,
                "file": self.target.file,
                "line": self.target.start_line + 1 if self.target.start_line is not None else None,
            },
            "max_depth": self.max_depth,
            "tree": [e.to_dict() for e in self.tree],
        }


@dataclass
class DepsEntry:
    """Single dependency entry with tree support."""

    depth: int
    node_id: str
    fqn: str
    file: str | None = None
    line: int | None = None
    children: list["DepsEntry"] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = {"depth": self.depth, "node_id": self.node_id, "fqn": self.fqn}
        if self.file is not None:
            d["file"] = self.file
        if self.line is not None:
            d["line"] = self.line + 1  # 0-based to 1-based for output
        if self.children:
            d["children"] = [c.to_dict() for c in self.children]
        return d


@dataclass
class DepsTreeResult:
    """Result of deps query with tree structure."""

    target: NodeData
    max_depth: int
    tree: list[DepsEntry] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "target": {
                "id": self.target.node_id,
                "kind": self.target.kind,
                "fqn": self.target.fqn,
                "file": self.target.file,
                "line": self.target.start_line + 1 if self.target.start_line is not None else None,
            },
            "max_depth": self.max_depth,
            "tree": [e.to_dict() for e in self.tree],
        }


@dataclass
class OwnersResult:
    """Result of owners query -- containment chain from target up to File."""

    chain: list[NodeData]

    def to_dict(self) -> dict:
        return {
            "chain": [
                {
                    "id": n.node_id,
                    "kind": n.kind,
                    "fqn": n.fqn,
                    "file": n.file,
                    "line": n.start_line + 1 if n.start_line is not None else None,
                }
                for n in self.chain
            ]
        }


@dataclass
class InheritEntry:
    """Single entry in an inheritance tree."""

    depth: int
    node_id: str
    fqn: str
    kind: str
    file: str | None = None
    line: int | None = None
    children: list["InheritEntry"] = field(default_factory=list)

    def to_dict(self) -> dict:
        d: dict = {
            "depth": self.depth,
            "node_id": self.node_id,
            "fqn": self.fqn,
            "kind": self.kind,
        }
        if self.file is not None:
            d["file"] = self.file
        if self.line is not None:
            d["line"] = self.line + 1  # 0-based to 1-based for output
        if self.children:
            d["children"] = [c.to_dict() for c in self.children]
        return d


@dataclass
class InheritTreeResult:
    """Result of inherit query with tree structure."""

    root: NodeData
    direction: str
    max_depth: int
    tree: list[InheritEntry] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "root": {
                "id": self.root.node_id,
                "kind": self.root.kind,
                "fqn": self.root.fqn,
                "file": self.root.file,
                "line": self.root.start_line + 1 if self.root.start_line is not None else None,
            },
            "direction": self.direction,
            "max_depth": self.max_depth,
            "tree": [e.to_dict() for e in self.tree],
        }


@dataclass
class OverrideEntry:
    """Single entry in an overrides tree."""

    depth: int
    node_id: str
    fqn: str
    file: str | None = None
    line: int | None = None
    children: list["OverrideEntry"] = field(default_factory=list)

    def to_dict(self) -> dict:
        d: dict = {
            "depth": self.depth,
            "node_id": self.node_id,
            "fqn": self.fqn,
        }
        if self.file is not None:
            d["file"] = self.file
        if self.line is not None:
            d["line"] = self.line + 1  # 0-based to 1-based for output
        if self.children:
            d["children"] = [c.to_dict() for c in self.children]
        return d


@dataclass
class OverridesTreeResult:
    """Result of overrides query with tree structure."""

    root: NodeData
    direction: str
    max_depth: int
    tree: list[OverrideEntry] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "root": {
                "id": self.root.node_id,
                "kind": self.root.kind,
                "fqn": self.root.fqn,
                "file": self.root.file,
                "line": self.root.start_line + 1 if self.root.start_line is not None else None,
            },
            "direction": self.direction,
            "max_depth": self.max_depth,
            "tree": [e.to_dict() for e in self.tree],
        }


# =========================================================================
# Context command models
# =========================================================================


@dataclass
class MemberRef:
    """Identifies a specific member usage within a relationship."""

    target_name: str
    target_fqn: str
    target_kind: str | None = None
    file: str | None = None
    line: int | None = None  # 0-based internally
    reference_type: str | None = None
    access_chain: str | None = None
    access_chain_symbol: str | None = None
    on_kind: str | None = None
    on_file: str | None = None
    on_line: int | None = None  # 0-based internally


@dataclass
class ArgumentInfo:
    """Argument-to-parameter mapping at a call site."""

    position: int
    param_name: str | None = None
    value_expr: str | None = None
    value_source: str | None = None
    value_type: str | None = None
    param_fqn: str | None = None
    value_ref_symbol: str | None = None
    source_chain: list | None = None


@dataclass
class ContextEntry:
    """Single context entry with tree support for used_by/uses trees."""

    depth: int
    node_id: str
    fqn: str
    kind: str | None = None
    file: str | None = None
    line: int | None = None  # 0-based internally
    signature: str | None = None
    children: list["ContextEntry"] = field(default_factory=list)
    implementations: list["ContextEntry"] = field(default_factory=list)
    via_interface: bool = False
    member_ref: MemberRef | None = None
    arguments: list[ArgumentInfo] = field(default_factory=list)
    result_var: str | None = None
    entry_type: str | None = None  # "call" or "local_variable"
    variable_name: str | None = None
    variable_symbol: str | None = None
    variable_type: str | None = None
    source_call: Optional["ContextEntry"] = None
    crossed_from: str | None = None
    ref_type: str | None = None  # "instantiation", "extends", etc.
    callee: str | None = None
    on: str | None = None  # receiver expression
    on_kind: str | None = None  # "property", "param", "local", "self"
    sites: list | None = None
    via: str | None = None
    property_name: str | None = None
    access_count: int | None = None
    method_count: int | None = None


@dataclass
class DefinitionInfo:
    """Structural definition metadata for a symbol."""

    fqn: str
    kind: str
    file: str | None = None
    line: int | None = None
    signature: str | None = None
    arguments: list[dict] = field(default_factory=list)
    return_type: dict | None = None
    declared_in: dict | None = None
    properties: list[dict] = field(default_factory=list)
    methods: list[dict] = field(default_factory=list)
    extends: str | None = None
    implements: list[str] = field(default_factory=list)
    uses_traits: list[str] = field(default_factory=list)
    value_kind: str | None = None
    type_info: dict | None = None
    source: dict | None = None
    constructor_deps: list[dict] = field(default_factory=list)


@dataclass
class ContextResult:
    """Result of context query with bidirectional tree structure."""

    target: NodeData
    max_depth: int
    used_by: list[ContextEntry] = field(default_factory=list)
    uses: list[ContextEntry] = field(default_factory=list)
    definition: DefinitionInfo | None = None
