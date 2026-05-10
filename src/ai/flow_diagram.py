"""ASCII flow diagram generator for LLM prompts."""

import logging
from dataclasses import dataclass, field

from ..db.query_runner import QueryRunner
from ..db.result_mapper import record_to_node
from ..models.node import NodeData
from .source_reader import SourceReader

logger = logging.getLogger(__name__)


@dataclass
class FlowStepInfo:
    """Resolved info for a single chain step."""
    fqn: str
    node_id: str
    role: str
    position: int
    node: NodeData | None = None
    source: str | None = None
    methods_used: list[str] = field(default_factory=list)
    classes_used: list[str] = field(default_factory=list)
    properties_read: list[str] = field(default_factory=list)


@dataclass
class FlowInfo:
    """Fully resolved flow with all context."""
    flow_id: str
    flow_type: str
    name: str
    entry_fqn: str
    entry_method: str
    entry_node: NodeData | None = None
    entry_method_node: NodeData | None = None
    entry_source: str | None = None
    entry_method_source: str | None = None
    route: str | None = None
    http_methods: list[str] | None = None
    command_name: str | None = None
    event_name: str | None = None
    message_class: str | None = None
    steps: list[FlowStepInfo] = field(default_factory=list)
    triggers: list[dict] = field(default_factory=list)


class FlowDiagramBuilder:
    """Builds ASCII flow diagrams from Neo4j flow + code graph data."""

    def __init__(self, runner: QueryRunner, reader: SourceReader):
        self._runner = runner
        self._reader = reader

    def resolve_flow(self, flow_id: str) -> FlowInfo | None:
        """Resolve a flow from Neo4j into a FlowInfo with all context."""
        # Get flow node
        rec = self._runner.execute_single(
            "MATCH (f:Flow {flow_id: $fid}) RETURN f", fid=flow_id
        )
        if not rec:
            logger.debug("Flow not found: %s", flow_id)
            return None

        f = rec["f"]
        info = FlowInfo(
            flow_id=f["flow_id"],
            flow_type=f["type"],
            name=f.get("name", ""),
            entry_fqn=f.get("entry_fqn", ""),
            entry_method=f.get("entry_method", ""),
            route=f.get("route"),
            http_methods=f.get("http_methods"),
            command_name=f.get("command_name"),
            event_name=f.get("event_name"),
            message_class=f.get("message_class"),
        )

        # Resolve entry method node
        entry_rec = self._runner.execute_single(
            "MATCH (f:Flow {flow_id: $fid})-[:FLOW_ENTRY]->(m:Node) RETURN m",
            fid=flow_id,
        )
        if entry_rec:
            info.entry_method_node = record_to_node(entry_rec, key="m")
            info.entry_method_source = self._reader.read_node_source(info.entry_method_node)

        # Resolve entry class node
        if info.entry_fqn:
            class_rec = self._runner.execute_single(
                "MATCH (n:Node {fqn: $fqn}) WHERE n.kind IN ['Class', 'Interface', 'Trait'] RETURN n",
                fqn=info.entry_fqn,
            )
            if class_rec:
                info.entry_node = record_to_node(class_rec)
                info.entry_source = self._reader.read_node_source(info.entry_node)

        # Resolve chain steps
        step_recs = self._runner.execute(
            """
            MATCH (f:Flow {flow_id: $fid})-[r:FLOW_STEP]->(n:Node)
            RETURN n, r.position AS position, r.role AS role
            ORDER BY r.position
            """,
            fid=flow_id,
        )
        for sr in step_recs:
            node = record_to_node(sr, key="n")
            step = FlowStepInfo(
                fqn=node.fqn,
                node_id=node.node_id,
                role=sr["role"],
                position=sr["position"],
                node=node,
                source=self._reader.read_node_source(node),
            )
            # Get what methods/classes this step's class uses
            self._resolve_step_usages(step)
            info.steps.append(step)

        # Resolve triggers
        trigger_recs = self._runner.execute(
            """
            MATCH (f:Flow {flow_id: $fid})-[r:FLOW_TRIGGERS]->(t:Flow)
            RETURN t.flow_id AS target_id, t.name AS target_name,
                   t.type AS target_type, r.trigger_type AS trigger_type, r.via AS via
            """,
            fid=flow_id,
        )
        for tr in trigger_recs:
            info.triggers.append({
                "target_id": tr["target_id"],
                "target_name": tr["target_name"],
                "target_type": tr["target_type"],
                "trigger_type": tr["trigger_type"],
                "via": tr["via"],
            })

        return info

    def _resolve_step_usages(self, step: FlowStepInfo) -> None:
        """Find what methods/classes/properties a chain step class uses."""
        # Get methods of this class that use other things
        recs = self._runner.execute(
            """
            MATCH (c:Node {node_id: $nid})-[:CONTAINS]->(m:Node {kind: 'Method'})-[:USES]->(t:Node)
            WHERE t.file IS NOT NULL
            RETURN DISTINCT t.fqn AS fqn, t.kind AS kind
            ORDER BY t.kind, t.fqn
            """,
            nid=step.node_id,
        )
        for r in recs:
            fqn = r["fqn"]
            kind = r["kind"]
            if kind == "Method":
                step.methods_used.append(fqn)
            elif kind in ("Class", "Interface", "Trait", "Enum"):
                step.classes_used.append(fqn)
            elif kind == "Property":
                step.properties_read.append(fqn)

    def build_diagram(self, info: FlowInfo) -> str:
        """Build ASCII flow diagram from resolved FlowInfo."""
        lines = []

        # Header
        header = self._build_header(info)
        lines.append(header)
        lines.append("")

        # Entry point
        entry_sig = self._get_signature(info.entry_method_node)
        entry_short = info.entry_fqn.rsplit("\\", 1)[-1]
        lines.append(f"  {entry_short}::{entry_sig}")

        # Chain steps
        triggers_shown = False
        for i, step in enumerate(info.steps):
            is_last = i == len(info.steps) - 1 and not info.triggers
            connector = "└" if is_last else "├"
            pipe = " " if is_last else "│"

            step_short = step.fqn.rsplit("\\", 1)[-1]
            lines.append(f"    │")
            lines.append(f"    {connector}─[{step.role}]→ {step_short}")

            # Show key usages of this step
            for method in step.methods_used[:5]:
                if method.startswith("App\\"):
                    method_short = method.rsplit("\\", 1)[-1]
                    lines.append(f"    {pipe}          ├─calls→ {method_short}")

            for cls in step.classes_used[:3]:
                if cls.startswith("App\\"):
                    cls_short = cls.rsplit("\\", 1)[-1]
                    lines.append(f"    {pipe}          ├─uses→ {cls_short}")

        # Show triggers after all chain steps (they're flow-level connections)
        if info.triggers:
            lines.append(f"    │")
            for i, trigger in enumerate(info.triggers):
                tgt_name = trigger.get("target_name", trigger["target_id"])
                t_type = trigger["trigger_type"]
                is_last_trigger = i == len(info.triggers) - 1
                connector = "└" if is_last_trigger else "├"
                lines.append(f"    {connector}──{t_type}──▶ {tgt_name}")

        return "\n".join(lines)

    def _build_header(self, info: FlowInfo) -> str:
        """Build the header line for the flow diagram."""
        if info.flow_type == "http":
            methods = ",".join(info.http_methods or ["ANY"])
            return f"FLOW: {methods} {info.route}  [{info.flow_type}]"
        elif info.flow_type == "cli":
            return f"FLOW: {info.command_name}  [{info.flow_type}]"
        elif info.flow_type == "message":
            msg = (info.message_class or "").rsplit("\\", 1)[-1]
            return f"FLOW: handle {msg}  [{info.flow_type}]"
        elif info.flow_type == "event":
            evt = (info.event_name or "").rsplit("\\", 1)[-1]
            return f"FLOW: on {evt}  [{info.flow_type}]"
        return f"FLOW: {info.flow_id}  [{info.flow_type}]"

    def _get_signature(self, node: NodeData | None) -> str:
        """Get method signature or fallback."""
        if node and node.signature:
            return node.signature
        if node:
            return f"{node.name}()"
        return "()"

    def build_code_context(self, info: FlowInfo, max_tokens: int = 16000) -> str:
        """Build code context blocks for LLM prompt, respecting token budget.

        Tier 1 (always): entry class + chain step classes
        Tier 2 (if fits): DTOs/entities referenced via uses edges
        """
        blocks = []
        total_tokens = 0

        # Tier 1: Entry class
        if info.entry_source:
            block = self._code_block(info.entry_fqn, "Entry Class", info.entry_source)
            tokens = len(block) // 4
            if total_tokens + tokens <= max_tokens:
                blocks.append(block)
                total_tokens += tokens

        # Tier 1: Chain step classes
        for step in info.steps:
            if step.source:
                block = self._code_block(step.fqn, f"Chain Step [{step.role}]", step.source)
                tokens = len(block) // 4
                if total_tokens + tokens <= max_tokens:
                    blocks.append(block)
                    total_tokens += tokens

        # Tier 2: Referenced types (DTOs, entities)
        seen = {info.entry_fqn} | {s.fqn for s in info.steps}
        for step in info.steps:
            for cls_fqn in step.classes_used:
                if cls_fqn in seen or not cls_fqn.startswith("App\\"):
                    continue
                rec = self._runner.execute_single(
                    "MATCH (n:Node {fqn: $fqn}) WHERE n.kind IN ['Class','Interface','Trait','Enum'] RETURN n",
                    fqn=cls_fqn,
                )
                if rec:
                    node = record_to_node(rec)
                    source = self._reader.read_node_source(node)
                    if source:
                        block = self._code_block(cls_fqn, f"Referenced {node.kind}", source)
                        tokens = len(block) // 4
                        if total_tokens + tokens <= max_tokens:
                            blocks.append(block)
                            total_tokens += tokens
                            seen.add(cls_fqn)
                        else:
                            break  # budget exceeded

        logger.debug("  Code context: %d blocks, ~%d tokens", len(blocks), total_tokens)
        return "\n\n".join(blocks)

    def _code_block(self, fqn: str, label: str, source: str) -> str:
        """Format a labeled PHP code block."""
        return f"=== {label}: {fqn} ===\n```php\n{source}\n```"
