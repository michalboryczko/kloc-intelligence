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
    file: Optional[str] = None
    line: Optional[int] = None
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
                "line": self.target.start_line + 1
                if self.target.start_line is not None
                else None,
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
    file: Optional[str] = None
    line: Optional[int] = None
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
                "line": self.target.start_line + 1
                if self.target.start_line is not None
                else None,
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
                    "line": n.start_line + 1
                    if n.start_line is not None
                    else None,
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
    file: Optional[str] = None
    line: Optional[int] = None
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
                "line": self.root.start_line + 1
                if self.root.start_line is not None
                else None,
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
    file: Optional[str] = None
    line: Optional[int] = None
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
                "line": self.root.start_line + 1
                if self.root.start_line is not None
                else None,
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
    target_kind: Optional[str] = None
    file: Optional[str] = None
    line: Optional[int] = None       # 0-based internally
    reference_type: Optional[str] = None
    access_chain: Optional[str] = None
    access_chain_symbol: Optional[str] = None
    on_kind: Optional[str] = None
    on_file: Optional[str] = None
    on_line: Optional[int] = None    # 0-based internally


@dataclass
class ArgumentInfo:
    """Argument-to-parameter mapping at a call site."""

    position: int
    param_name: Optional[str] = None
    value_expr: Optional[str] = None
    value_source: Optional[str] = None
    value_type: Optional[str] = None
    param_fqn: Optional[str] = None
    value_ref_symbol: Optional[str] = None
    source_chain: Optional[list] = None


@dataclass
class ContextEntry:
    """Single context entry with tree support for used_by/uses trees."""

    depth: int
    node_id: str
    fqn: str
    kind: Optional[str] = None
    file: Optional[str] = None
    line: Optional[int] = None              # 0-based internally
    signature: Optional[str] = None
    children: list["ContextEntry"] = field(default_factory=list)
    implementations: list["ContextEntry"] = field(default_factory=list)
    via_interface: bool = False
    member_ref: Optional[MemberRef] = None
    arguments: list[ArgumentInfo] = field(default_factory=list)
    result_var: Optional[str] = None
    entry_type: Optional[str] = None        # "call" or "local_variable"
    variable_name: Optional[str] = None
    variable_symbol: Optional[str] = None
    variable_type: Optional[str] = None
    source_call: Optional["ContextEntry"] = None
    crossed_from: Optional[str] = None
    ref_type: Optional[str] = None          # "instantiation", "extends", etc.
    callee: Optional[str] = None
    on: Optional[str] = None                # receiver expression
    on_kind: Optional[str] = None           # "property", "param", "local", "self"
    sites: Optional[list] = None
    via: Optional[str] = None
    property_name: Optional[str] = None
    access_count: Optional[int] = None
    method_count: Optional[int] = None


@dataclass
class DefinitionInfo:
    """Structural definition metadata for a symbol."""

    fqn: str
    kind: str
    file: Optional[str] = None
    line: Optional[int] = None
    signature: Optional[str] = None
    arguments: list[dict] = field(default_factory=list)
    return_type: Optional[dict] = None
    declared_in: Optional[dict] = None
    properties: list[dict] = field(default_factory=list)
    methods: list[dict] = field(default_factory=list)
    extends: Optional[str] = None
    implements: list[str] = field(default_factory=list)
    uses_traits: list[str] = field(default_factory=list)
    value_kind: Optional[str] = None
    type_info: Optional[dict] = None
    source: Optional[dict] = None
    constructor_deps: list[dict] = field(default_factory=list)


@dataclass
class ContextResult:
    """Result of context query with bidirectional tree structure."""

    target: NodeData
    max_depth: int
    used_by: list[ContextEntry] = field(default_factory=list)
    uses: list[ContextEntry] = field(default_factory=list)
    definition: Optional[DefinitionInfo] = None
