"""Contract-compliant output model for the context command.

Mirrors kloc-contracts/kloc-cli-context.json exactly.
All field names match the JSON schema (camelCase where needed).
All line numbers are 1-based (converted at construction time).

The output model is the canonical intermediate representation between
internal query results (ContextResult) and JSON output.

Usage:
    output = ContextOutput.from_result(result)
    json_output = json.dumps(output.to_dict(), indent=2)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .results import (
    ArgumentInfo,
    ContextEntry,
    ContextResult,
    DefinitionInfo,
    MemberRef,
)
from .node import NodeData


def _normalize_param_fqn(param_fqn: Optional[str]) -> Optional[str]:
    """Normalize param FQN by stripping __construct() from promoted parameters.

    'App\\Entity\\Order::__construct().$id' -> 'App\\Entity\\Order::$id'

    Regular method params are untouched:
    'App\\Service\\OrderService::createOrder().$input' stays the same.
    """
    if not param_fqn or "::" not in param_fqn:
        return param_fqn
    ns_class, member = param_fqn.rsplit("::", 1)
    if member.startswith("__construct()."):
        member = member[len("__construct()."):]
        return f"{ns_class}::{member}"
    return param_fqn


def _shorten_param_key(param_fqn: Optional[str], param_name: Optional[str], position: int) -> str:
    """Shorten param key for flat args format: 'Namespace\\Class::method().$param' -> 'Class::method().$param'.

    Strips the namespace prefix but keeps the rest intact.
    Promoted constructor params should already be resolved to Property FQNs
    at the argument-building level (e.g., 'Order::$id' not 'Order::__construct().$id').
    Property USES context passes raw param_fqn (with __construct()) which is correct.
    """
    key = param_fqn or param_name or f"arg[{position}]"
    if param_fqn and "::" in param_fqn:
        ns_class, member = param_fqn.rsplit("::", 1)
        short_class = ns_class.rsplit("\\", 1)[-1] if "\\" in ns_class else ns_class
        key = f"{short_class}::{member}"
    return key


@dataclass
class OutputArgumentInfo:
    """Argument-to-parameter mapping at a call site."""

    position: int
    param_name: Optional[str]
    value_expr: Optional[str]
    value_source: Optional[str]

    value_type: Optional[str] = None
    param_fqn: Optional[str] = None
    value_ref_symbol: Optional[str] = None
    source_chain: Optional[list] = None

    @classmethod
    def from_info(cls, info: ArgumentInfo) -> OutputArgumentInfo:
        return cls(
            position=info.position,
            param_name=info.param_name,
            value_expr=info.value_expr,
            value_source=info.value_source,
            value_type=info.value_type,
            param_fqn=_normalize_param_fqn(info.param_fqn),
            value_ref_symbol=info.value_ref_symbol,
            source_chain=info.source_chain,
        )

    def to_dict(self) -> dict:
        d: dict = {
            "position": self.position,
            "param_name": self.param_name,
            "value_expr": self.value_expr,
            "value_source": self.value_source,
        }
        if self.value_type is not None:
            d["value_type"] = self.value_type
        if self.param_fqn is not None:
            d["param_fqn"] = self.param_fqn
        if self.value_ref_symbol is not None:
            d["value_ref_symbol"] = self.value_ref_symbol
        if self.source_chain is not None:
            d["source_chain"] = self.source_chain
        return d


@dataclass
class OutputMemberRef:
    """Identifies a specific member usage within a relationship."""

    target_name: str
    target_fqn: str
    target_kind: Optional[str]
    file: Optional[str]
    line: Optional[int]  # 1-based

    reference_type: Optional[str] = None
    access_chain: Optional[str] = None
    access_chain_symbol: Optional[str] = None
    on_kind: Optional[str] = None
    on_file: Optional[str] = None
    on_line: Optional[int] = None  # 1-based

    @classmethod
    def from_ref(cls, ref: MemberRef) -> OutputMemberRef:
        return cls(
            target_name=ref.target_name,
            target_fqn=ref.target_fqn,
            target_kind=ref.target_kind,
            file=ref.file,
            line=ref.line + 1 if ref.line is not None else None,
            reference_type=ref.reference_type,
            access_chain=ref.access_chain,
            access_chain_symbol=ref.access_chain_symbol,
            on_kind=ref.on_kind,
            on_file=ref.on_file,
            on_line=ref.on_line + 1 if ref.on_line is not None else None,
        )

    def to_dict(self) -> dict:
        d: dict = {
            "target_name": self.target_name,
            "target_fqn": self.target_fqn,
            "target_kind": self.target_kind,
            "file": self.file,
            "line": self.line,
        }
        if self.reference_type:
            d["reference_type"] = self.reference_type
        if self.access_chain:
            d["access_chain"] = self.access_chain
        if self.access_chain_symbol:
            d["access_chain_symbol"] = self.access_chain_symbol
        if self.on_kind:
            d["on_kind"] = self.on_kind
        if self.on_file:
            d["on_file"] = self.on_file
        if self.on_line is not None:
            d["on_line"] = self.on_line
        return d


@dataclass
class OutputEntry:
    """Recursive tree entry representing a caller or callee relationship.

    Fields vary by context mode (method vs class) and entry kind.
    The from_entry() factory applies mode-dependent field suppression.
    """

    # Required fields (always present in dict output)
    depth: int
    fqn: str
    kind: Optional[str]
    file: Optional[str]
    line: Optional[int]  # 1-based, removed from dict when sites present
    children: list[OutputEntry]

    # Signature (mode-dependent inclusion)
    signature: Optional[str] = None

    # Reference type
    ref_type: Optional[str] = None

    # USED BY direction
    via_interface: bool = False
    via: Optional[str] = None

    # USES direction - implementations
    implementations: Optional[list[OutputEntry]] = None

    # Member reference (method-level context only)
    member_ref: Optional[OutputMemberRef] = None

    # Arguments - two formats depending on context mode
    arguments: Optional[list[OutputArgumentInfo]] = None  # method-level: rich list
    args: Optional[dict[str, str]] = None  # class-level: flat key->value

    # Call metadata
    callee: Optional[str] = None
    on: Optional[str] = None
    on_kind: Optional[str] = None
    result_var: Optional[str] = None

    # Variable-centric flow
    entry_type: Optional[str] = None
    variable_name: Optional[str] = None
    variable_symbol: Optional[str] = None
    variable_type: Optional[str] = None
    source_call: Optional[OutputEntry] = None

    # Cross-method boundary
    crossed_from: Optional[str] = None

    # Multi-site
    sites: Optional[list] = None

    # Property group
    property_name: Optional[str] = None
    access_count: Optional[int] = None
    method_count: Optional[int] = None

    @classmethod
    def from_entry(cls, entry: ContextEntry, *, class_level: bool = False) -> OutputEntry:
        """Convert internal ContextEntry applying mode-dependent rules."""
        # Line number: 0-based internal -> 1-based output
        line = entry.line + 1 if entry.line is not None else None

        # Signature: mode-dependent
        signature = None
        if class_level:
            if entry.signature and entry.ref_type in ("override", "inherited"):
                signature = entry.signature
        else:
            if entry.signature and not entry.ref_type:
                signature = entry.signature
            elif entry.signature and entry.ref_type in ("override", "inherited"):
                signature = entry.signature

        # Children (recursive)
        children = [cls.from_entry(c, class_level=class_level) for c in entry.children]

        # Implementations
        implementations = None
        if entry.implementations:
            implementations = [cls.from_entry(impl, class_level=class_level) for impl in entry.implementations]

        # Member ref (method-level only, only when no ref_type)
        member_ref = None
        if not class_level and entry.member_ref and not entry.ref_type:
            member_ref = OutputMemberRef.from_ref(entry.member_ref)

        # Arguments - format depends on mode
        arguments = None
        args = None
        if entry.arguments:
            if entry.ref_type or class_level:
                # Flat format for class-level context or entries with ref_type
                flat: dict[str, str] = {}
                for a in entry.arguments:
                    key = _shorten_param_key(a.param_fqn, a.param_name, a.position)
                    flat[key] = a.value_expr or "?"
                if flat:
                    args = flat
            else:
                arguments = [OutputArgumentInfo.from_info(a) for a in entry.arguments]

        # Source call (recursive)
        source_call = None
        if entry.source_call:
            source_call = cls.from_entry(entry.source_call, class_level=class_level)

        # Crossed from: suppress in class-level at depth < 2
        crossed_from = None
        if entry.crossed_from:
            if not class_level or entry.depth >= 2:
                crossed_from = entry.crossed_from

        # Callee: only for method_call ref type
        callee = entry.callee if entry.ref_type == "method_call" else None

        # Sites: when present, line is omitted from dict output
        # (we keep original line in dataclass, pop in to_dict)
        sites = entry.sites

        return cls(
            depth=entry.depth,
            fqn=entry.fqn,
            kind=entry.kind,
            file=entry.file,
            line=line,
            children=children,
            signature=signature,
            ref_type=entry.ref_type,
            via_interface=entry.via_interface or False,
            via=entry.via,
            implementations=implementations,
            member_ref=member_ref,
            arguments=arguments,
            args=args,
            callee=callee,
            on=entry.on,
            on_kind=entry.on_kind,
            result_var=entry.result_var,
            entry_type=entry.entry_type,
            variable_name=entry.variable_name,
            variable_symbol=entry.variable_symbol,
            variable_type=entry.variable_type,
            source_call=source_call,
            crossed_from=crossed_from,
            sites=sites,
            property_name=entry.property_name,
            access_count=entry.access_count,
            method_count=entry.method_count,
        )

    def to_dict(self) -> dict:
        """Serialize to contract-compliant dict.

        Matches context_tree_to_dict() output exactly:
        - file and line are always present (even when None)
        - line is removed when sites is present
        - Field naming: ref_type->refType, on_kind->onKind, etc.
        """
        d: dict = {
            "depth": self.depth,
            "fqn": self.fqn,
            "kind": self.kind,
            "file": self.file,
            "line": self.line,
            "children": [c.to_dict() for c in self.children],
        }

        # Signature
        if self.signature:
            d["signature"] = self.signature

        # Implementations (USES direction)
        if self.implementations:
            d["implementations"] = [impl.to_dict() for impl in self.implementations]

        # Via interface flag (USED BY direction)
        if self.via_interface:
            d["via_interface"] = True

        # Member ref
        if self.member_ref:
            d["member_ref"] = self.member_ref.to_dict()

        # Arguments (two formats)
        if self.arguments:
            d["arguments"] = [a.to_dict() for a in self.arguments]
        if self.args:
            d["args"] = self.args

        # Result var
        if self.result_var:
            d["result_var"] = self.result_var

        # Variable-centric flow
        if self.entry_type:
            d["entry_type"] = self.entry_type
        if self.variable_name:
            d["variable_name"] = self.variable_name
        if self.variable_symbol:
            d["variable_symbol"] = self.variable_symbol
        if self.variable_type:
            d["variable_type"] = self.variable_type
        if self.source_call:
            d["source_call"] = self.source_call.to_dict()

        # Cross-method boundary
        if self.crossed_from:
            d["crossed_from"] = self.crossed_from

        # Reference type and related fields
        if self.ref_type:
            d["refType"] = self.ref_type
        if self.callee:
            d["callee"] = self.callee
        if self.on:
            d["on"] = self.on
        if self.on_kind:
            d["onKind"] = self.on_kind

        # Sites (when present, remove line)
        # Note: site lines are kept as-is (0-based) matching kloc-cli convention.
        # Only the entry-level 'line' field is 1-based.
        if self.sites:
            d["sites"] = self.sites
            d.pop("line", None)

        # Via
        if self.via:
            d["via"] = self.via

        # Property group
        if self.property_name:
            d["property"] = self.property_name
        if self.access_count is not None:
            d["accessCount"] = self.access_count
        if self.method_count is not None:
            d["methodCount"] = self.method_count

        return d


@dataclass
class OutputTarget:
    """The queried target symbol."""

    fqn: str
    file: Optional[str]
    line: Optional[int]  # 1-based
    signature: Optional[str] = None

    @classmethod
    def from_node(cls, node: NodeData) -> OutputTarget:
        return cls(
            fqn=node.fqn,
            file=node.file,
            line=node.start_line + 1 if node.start_line is not None else None,
            signature=node.signature,
        )

    def to_dict(self) -> dict:
        d: dict = {"fqn": self.fqn, "file": self.file, "line": self.line}
        if self.signature:
            d["signature"] = self.signature
        return d


@dataclass
class OutputDefinition:
    """Structural definition metadata for the target symbol."""

    fqn: str
    kind: str
    file: Optional[str] = None
    line: Optional[int] = None  # 1-based

    # Method/function fields
    signature: Optional[str] = None
    arguments: Optional[list[dict]] = None
    return_type: Optional[dict] = None

    # Class/interface fields
    properties: Optional[list[dict]] = None
    methods: Optional[list[dict]] = None
    extends: Optional[str] = None
    implements: Optional[list[str]] = None
    uses_traits: Optional[list[str]] = None
    constructor_deps: Optional[list[dict]] = None

    # Property fields
    type_name: Optional[str] = None  # serialized as "type" (string for Property)
    visibility: Optional[str] = None
    promoted: Optional[bool] = None
    readonly: Optional[bool] = None
    static: Optional[bool] = None

    # Value fields
    value_kind: Optional[str] = None
    type_info: Optional[dict] = None  # serialized as "type" (object for Value)
    source: Optional[dict] = None

    # Containment
    declared_in: Optional[dict] = None

    @classmethod
    def from_info(cls, info: DefinitionInfo) -> OutputDefinition:
        """Convert internal DefinitionInfo to output model."""
        # Property type extraction from return_type
        type_name = None
        visibility = None
        promoted = None
        readonly = None
        static = None
        return_type = None

        if info.kind == "Property" and info.return_type:
            rt = info.return_type
            type_name = rt.get("name", rt.get("fqn"))
            visibility = rt.get("visibility")
            promoted = rt.get("promoted") or None
            readonly = rt.get("readonly") or None
            static = rt.get("static") or None
        elif info.return_type:
            return_type = info.return_type

        # DeclaredIn: only for non-class kinds, convert line to 1-based
        declared_in = None
        if info.declared_in and info.kind not in ("Class", "Interface", "Trait", "Enum"):
            raw_line = info.declared_in.get("line")
            declared_in = {
                "fqn": info.declared_in.get("fqn"),
                "file": info.declared_in.get("file"),
                "line": raw_line + 1 if raw_line is not None else None,
            }

        return cls(
            fqn=info.fqn,
            kind=info.kind,
            file=info.file,
            line=info.line + 1 if info.line is not None else None,
            signature=info.signature,
            arguments=info.arguments or None,
            return_type=return_type,
            properties=info.properties or None,
            methods=info.methods or None,
            extends=info.extends,
            implements=info.implements or None,
            uses_traits=info.uses_traits or None,
            constructor_deps=info.constructor_deps or None,
            type_name=type_name,
            visibility=visibility,
            promoted=promoted,
            readonly=readonly,
            static=static,
            value_kind=info.value_kind,
            type_info=info.type_info,
            source=info.source,
            declared_in=declared_in,
        )

    def to_dict(self) -> dict:
        d: dict = {
            "fqn": self.fqn,
            "kind": self.kind,
            "file": self.file,
            "line": self.line,
        }
        if self.signature:
            d["signature"] = self.signature
        if self.arguments:
            d["arguments"] = self.arguments
        # Property-specific: type from return_type metadata
        if self.kind == "Property":
            if self.type_name:
                d["type"] = self.type_name
            if self.visibility:
                d["visibility"] = self.visibility
            if self.promoted:
                d["promoted"] = True
            if self.readonly:
                d["readonly"] = True
            if self.static:
                d["static"] = True
        elif self.return_type:
            d["returnType"] = self.return_type
        # DeclaredIn
        if self.declared_in:
            d["declaredIn"] = self.declared_in
        # Class/interface structure
        if self.properties:
            d["properties"] = self.properties
        if self.methods:
            d["methods"] = self.methods
        if self.extends:
            d["extends"] = self.extends
        if self.implements:
            d["implements"] = self.implements
        if self.uses_traits:
            d["uses_traits"] = self.uses_traits
        # Value-specific
        if self.value_kind:
            d["value_kind"] = self.value_kind
        if self.type_info:
            d["type"] = self.type_info
        if self.source:
            d["source"] = self.source
        if self.constructor_deps:
            d["constructorDeps"] = self.constructor_deps
        return d


@dataclass
class ContextOutput:
    """Contract-compliant context command output.

    Mirrors kloc-contracts/kloc-cli-context.json exactly.
    All field names match the JSON schema (camelCase where needed).
    All line numbers are 1-based (converted at construction time).
    """

    target: OutputTarget
    max_depth: int
    used_by: list[OutputEntry]
    uses: list[OutputEntry]
    definition: Optional[OutputDefinition] = None

    @classmethod
    def from_result(cls, result: ContextResult) -> ContextOutput:
        """Convert internal ContextResult to contract-compliant output.

        This is the SINGLE conversion point. All line-number adjustments,
        field renaming, and mode-dependent logic happen here.
        """
        target_kind = result.target.kind if result.target else None
        class_level = target_kind in ("Class", "Interface", "Trait", "Enum", "Property")

        return cls(
            target=OutputTarget.from_node(result.target),
            max_depth=result.max_depth,
            used_by=[OutputEntry.from_entry(e, class_level=class_level) for e in result.used_by],
            uses=[OutputEntry.from_entry(e, class_level=class_level) for e in result.uses],
            definition=OutputDefinition.from_info(result.definition) if result.definition else None,
        )

    def to_dict(self) -> dict:
        """Serialize to JSON-compatible dict matching the contract schema."""
        d: dict = {
            "target": self.target.to_dict(),
            "maxDepth": self.max_depth,
            "usedBy": [e.to_dict() for e in self.used_by],
            "uses": [e.to_dict() for e in self.uses],
        }
        if self.definition:
            d["definition"] = self.definition.to_dict()
        return d
