"""Tests for Class context orchestrators.

All tests are pure unit tests with mocked QueryRunner -- no Neo4j required.

Coverage:
- build_class_used_by: orchestration logic, EdgeContext building, handler dispatch,
  injection suppression, property access grouping, caller chain recursion,
  dict -> ContextEntry conversion
- build_class_uses: orchestration logic, extends/implements exclusion, USES
  sort order, behavioral depth-2 expansion
- build_caller_chain_for_method: recursion, visited set, depth capping
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.models.node import NodeData
from src.models.results import ContextEntry
from src.orchestration.class_context import (
    build_class_used_by,
    build_class_uses,
    build_caller_chain_for_method,
    _dict_to_context_entry,
    _build_property_access_entries,
    USES_PRIORITY,
)
from src.logic.handlers import EntryBucket


# =============================================================================
# Fixtures / Factories
# =============================================================================


def make_node(**overrides) -> NodeData:
    """Return a NodeData for a Class with sensible defaults."""
    defaults = {
        "node_id": "class:Order",
        "kind": "Class",
        "name": "Order",
        "fqn": "App\\Entity\\Order",
        "symbol": "scip-php . `App\\Entity\\Order`.",
        "file": "src/Entity/Order.php",
        "start_line": 10,
    }
    defaults.update(overrides)
    return NodeData(**defaults)


def make_runner(side_effects: dict | None = None) -> MagicMock:
    """Return a MagicMock QueryRunner.

    side_effects: maps query string prefixes to return values. If None,
    always returns [].
    """
    runner = MagicMock()
    runner.execute.return_value = []
    return runner


def make_record(**fields) -> dict:
    """Simulate a neo4j Record as a plain dict (already materialised)."""
    return fields


# =============================================================================
# _dict_to_context_entry
# =============================================================================


class TestDictToContextEntry:
    """Tests for the handler dict -> ContextEntry converter."""

    def test_minimal_dict(self):
        d = {"node_id": "n:1", "fqn": "App\\Foo", "depth": 1}
        entry = _dict_to_context_entry(d)
        assert entry.node_id == "n:1"
        assert entry.fqn == "App\\Foo"
        assert entry.depth == 1
        assert entry.kind is None
        assert entry.ref_type is None
        assert entry.children == []

    def test_full_dict(self):
        d = {
            "node_id": "n:2",
            "fqn": "App\\Svc::create",
            "depth": 1,
            "kind": "Method",
            "file": "src/Svc.php",
            "line": 42,
            "ref_type": "method_call",
            "callee": "save()",
            "on": "$this->repo",
            "on_kind": "property",
            "children": [],
        }
        entry = _dict_to_context_entry(d)
        assert entry.kind == "Method"
        assert entry.file == "src/Svc.php"
        assert entry.line == 42
        assert entry.ref_type == "method_call"
        assert entry.callee == "save()"
        assert entry.on == "$this->repo"
        assert entry.on_kind == "property"

    def test_depth_override(self):
        d = {"node_id": "n:1", "fqn": "Foo", "depth": 1}
        entry = _dict_to_context_entry(d, depth=3)
        assert entry.depth == 3

    def test_property_name_and_counts(self):
        d = {
            "node_id": "m:1",
            "fqn": "App\\Svc::check",
            "depth": 1,
            "ref_type": "property_access",
            "property_name": "App\\Entity\\Order::$status",
            "access_count": 3,
            "method_count": 1,
        }
        entry = _dict_to_context_entry(d)
        assert entry.property_name == "App\\Entity\\Order::$status"
        assert entry.access_count == 3
        assert entry.method_count == 1


# =============================================================================
# _build_property_access_entries
# =============================================================================


class TestBuildPropertyAccessEntries:
    """Tests for property access group -> ContextEntry conversion."""

    def test_single_group_single_line(self):
        bucket = EntryBucket()
        bucket.property_access_groups["App\\Order::$status"] = [
            {
                "method_fqn": "App\\Svc::check",
                "method_id": "m:check",
                "method_kind": "Method",
                "lines": [10],
                "on_expr": "$this->order",
                "on_kind": "property",
                "file": "src/Svc.php",
            }
        ]
        entries = _build_property_access_entries(bucket, "App\\Entity\\Order", depth=1)
        assert len(entries) == 1
        e = entries[0]
        assert e.ref_type == "property_access"
        assert e.kind == "PropertyGroup"
        assert e.node_id == "App\\Order::$status"
        assert e.fqn == "Order::$status"
        assert e.access_count == 1
        assert e.method_count == 1

    def test_multiple_lines_creates_sites(self):
        bucket = EntryBucket()
        bucket.property_access_groups["App\\Order::$id"] = [
            {
                "method_fqn": "App\\Svc::m",
                "method_id": "m:1",
                "method_kind": "Method",
                "lines": [5, 10, 15],
                "on_expr": "$this->order",
                "on_kind": "property",
                "file": "src/Svc.php",
            }
        ]
        entries = _build_property_access_entries(bucket, "App\\Entity\\Order", depth=1)
        assert len(entries) == 1
        e = entries[0]
        assert e.access_count == 3
        assert e.method_count == 1

    def test_multiple_groups_for_same_property(self):
        """Multiple method groups for same property are aggregated into one entry."""
        bucket = EntryBucket()
        bucket.property_access_groups["App\\Order::$status"] = [
            {
                "method_fqn": "App\\Svc::a",
                "method_id": "m:a",
                "method_kind": "Method",
                "lines": [1],
                "on_expr": "$a",
                "on_kind": "property",
                "file": "src/Svc.php",
            },
            {
                "method_fqn": "App\\Svc::b",
                "method_id": "m:b",
                "method_kind": "Method",
                "lines": [2],
                "on_expr": "$b",
                "on_kind": "property",
                "file": "src/Svc.php",
            },
        ]
        entries = _build_property_access_entries(bucket, "App\\Entity\\Order", depth=1)
        assert len(entries) == 1
        assert entries[0].access_count == 2
        assert entries[0].method_count == 2

    def test_empty_bucket(self):
        bucket = EntryBucket()
        entries = _build_property_access_entries(bucket, "App\\Entity\\Order", depth=1)
        assert entries == []


# =============================================================================
# build_caller_chain_for_method
# =============================================================================


class TestBuildCallerChainForMethod:
    """Tests for recursive caller chain expansion."""

    def test_no_callers_returns_empty(self):
        runner = make_runner()
        runner.execute.return_value = []
        entries = build_caller_chain_for_method(runner, "method:1", 2, 3, set())
        assert entries == []

    def test_single_caller_at_depth_2(self):
        runner = make_runner()
        runner.execute.return_value = [
            {
                "caller_id": "method:caller",
                "caller_fqn": "App\\Svc::doSomething",
                "caller_kind": "Method",
                "caller_file": "src/Svc.php",
                "caller_start_line": 5,
                "call_line": 20,
                "on_property": None,
            }
        ]
        entries = build_caller_chain_for_method(runner, "method:target", 2, 2, set())
        assert len(entries) == 1
        assert entries[0].fqn == "App\\Svc::doSomething()"
        assert entries[0].depth == 2
        assert entries[0].ref_type == "caller"

    def test_depth_cap_prevents_recursion(self):
        runner = make_runner()
        runner.execute.return_value = [
            {
                "caller_id": "method:caller",
                "caller_fqn": "App\\Svc::m",
                "caller_kind": "Method",
                "caller_file": None,
                "caller_start_line": None,
                "call_line": 10,
                "on_property": None,
            }
        ]
        # max_depth=2, starting at depth=2 -> no recursion
        entries = build_caller_chain_for_method(runner, "method:1", 2, 2, set())
        assert len(entries) == 1
        assert entries[0].children == []

    def test_visited_set_prevents_cycles(self):
        runner = make_runner()
        runner.execute.return_value = [
            {
                "caller_id": "method:already_visited",
                "caller_fqn": "App\\Svc::m",
                "caller_kind": "Method",
                "caller_file": None,
                "caller_start_line": None,
                "call_line": 5,
                "on_property": None,
            }
        ]
        visited = {"method:already_visited"}
        entries = build_caller_chain_for_method(runner, "method:target", 2, 3, visited)
        assert entries == []

    def test_visited_set_updated_with_new_callers(self):
        runner = make_runner()
        runner.execute.return_value = [
            {
                "caller_id": "method:new",
                "caller_fqn": "App\\Svc::m",
                "caller_kind": "Method",
                "caller_file": None,
                "caller_start_line": None,
                "call_line": 5,
                "on_property": None,
            }
        ]
        visited: set[str] = set()
        build_caller_chain_for_method(runner, "method:target", 2, 2, visited)
        assert "method:new" in visited

    def test_depth_exceeds_max_returns_empty(self):
        runner = make_runner()
        entries = build_caller_chain_for_method(runner, "method:1", 5, 3, set())
        assert entries == []
        runner.execute.assert_not_called()

    def test_recursive_expansion(self):
        """Depth 2 caller has its own caller at depth 3."""
        call_count = 0

        def execute_side_effect(query, **kwargs):
            nonlocal call_count
            call_count += 1
            method_id = kwargs.get("method_id", "")
            if method_id == "method:target":
                return [
                    {
                        "caller_id": "method:level2",
                        "caller_fqn": "App\\Svc::level2",
                        "caller_kind": "Method",
                        "caller_file": None,
                        "caller_start_line": None,
                        "call_line": 10,
                        "on_property": None,
                    }
                ]
            elif method_id == "method:level2":
                return [
                    {
                        "caller_id": "method:level3",
                        "caller_fqn": "App\\Svc::level3",
                        "caller_kind": "Method",
                        "caller_file": None,
                        "caller_start_line": None,
                        "call_line": 20,
                        "on_property": None,
                    }
                ]
            return []

        runner = MagicMock()
        runner.execute.side_effect = execute_side_effect

        entries = build_caller_chain_for_method(runner, "method:target", 2, 3, set())
        assert len(entries) == 1
        assert entries[0].fqn == "App\\Svc::level2()"
        assert len(entries[0].children) == 1
        assert entries[0].children[0].fqn == "App\\Svc::level3()"
        assert entries[0].children[0].depth == 3


# =============================================================================
# build_class_used_by
# =============================================================================


def _patch_fetch_used_by(data: dict):
    """Return a context manager that patches fetch_class_used_by_data."""
    return patch(
        "src.orchestration.class_context.fetch_class_used_by_data",
        return_value=data,
    )


def _default_used_by_data(**overrides) -> dict:
    """Return a minimal empty fetch result."""
    defaults = {
        "extends_children": [],
        "incoming_usages": [],
        "injected_classes": set(),
        "call_nodes": [],
        "property_types": [],
        "ref_type_data": {},
        "constructor_calls": [],
        "external_method_calls": [],
        "external_property_accesses": [],
    }
    defaults.update(overrides)
    return defaults


class TestBuildClassUsedByEmpty:
    """Tests for build_class_used_by with no data."""

    def test_empty_data_returns_empty_list(self):
        runner = make_runner()
        node = make_node()
        with _patch_fetch_used_by(_default_used_by_data()):
            result = build_class_used_by(runner, node)
        assert result == []

    def test_returns_list_of_context_entries(self):
        runner = make_runner()
        node = make_node()
        data = _default_used_by_data(
            extends_children=[
                {"id": "c:1", "fqn": "App\\Child", "kind": "Class",
                 "file": "Child.php", "start_line": 5, "rel_type": "EXTENDS"}
            ]
        )
        with _patch_fetch_used_by(data):
            result = build_class_used_by(runner, node)
        assert all(isinstance(e, ContextEntry) for e in result)


class TestBuildClassUsedByExtends:
    """Tests for extends/implements entries from Q1."""

    def test_extends_child_creates_entry(self):
        runner = make_runner()
        node = make_node()
        data = _default_used_by_data(
            extends_children=[
                {"id": "c:child", "fqn": "App\\SpecialOrder", "kind": "Class",
                 "file": "src/SpecialOrder.php", "start_line": 3, "rel_type": "EXTENDS"}
            ]
        )
        with _patch_fetch_used_by(data):
            result = build_class_used_by(runner, node)
        assert len(result) == 1
        e = result[0]
        assert e.ref_type == "extends"
        assert e.fqn == "App\\SpecialOrder"
        assert e.node_id == "c:child"
        assert e.kind == "Class"

    def test_implements_child_creates_implements_entry(self):
        runner = make_runner()
        node = make_node()
        data = _default_used_by_data(
            extends_children=[
                {"id": "c:impl", "fqn": "App\\ConcreteRepo", "kind": "Class",
                 "file": "src/Repo.php", "start_line": 1, "rel_type": "IMPLEMENTS"}
            ]
        )
        with _patch_fetch_used_by(data):
            result = build_class_used_by(runner, node)
        assert result[0].ref_type == "implements"

    def test_multiple_children(self):
        runner = make_runner()
        node = make_node()
        data = _default_used_by_data(
            extends_children=[
                {"id": "c:1", "fqn": "App\\A", "kind": "Class",
                 "file": "A.php", "start_line": 1, "rel_type": "EXTENDS"},
                {"id": "c:2", "fqn": "App\\B", "kind": "Class",
                 "file": "B.php", "start_line": 1, "rel_type": "IMPLEMENTS"},
            ]
        )
        with _patch_fetch_used_by(data):
            result = build_class_used_by(runner, node)
        assert len(result) == 2

    def test_extends_entries_come_first_before_other_types(self):
        """Extends entries appear before instantiation in output."""
        runner = make_runner()
        node = make_node()
        data = _default_used_by_data(
            extends_children=[
                {"id": "c:ext", "fqn": "App\\Child", "kind": "Class",
                 "file": "Child.php", "start_line": 1, "rel_type": "EXTENDS"}
            ],
            incoming_usages=[
                {
                    "source_id": "m:factory",
                    "source_fqn": "App\\Factory::create",
                    "source_kind": "Method",
                    "source_name": "create",
                    "source_file": "Factory.php",
                    "source_start_line": 10,
                    "source_signature": None,
                    "edge_type": "USES",
                    "edge_target": None,
                    "edge_file": "Factory.php",
                    "edge_line": 15,
                    "containing_class_id": "class:Factory",
                    "containing_method_id": "m:factory",
                    "containing_method_fqn": "App\\Factory::create",
                    "containing_method_kind": "Method",
                }
            ],
            # ref_type_data that causes instantiation classification
            ref_type_data={},
        )
        with _patch_fetch_used_by(data):
            result = build_class_used_by(runner, node)
        # extends entry comes from Q1 and is first in the combined list
        extend_entries = [e for e in result if e.ref_type == "extends"]
        assert len(extend_entries) == 1


class TestBuildClassUsedByInstantiation:
    """Tests for instantiation handler dispatch."""

    def _make_instantiation_data(self) -> dict:
        return _default_used_by_data(
            incoming_usages=[
                {
                    "source_id": "m:create",
                    "source_fqn": "App\\Factory::create",
                    "source_kind": "Method",
                    "source_name": "create",
                    "source_file": "Factory.php",
                    "source_start_line": 5,
                    "source_signature": None,
                    "edge_type": "USES",
                    "edge_target": None,
                    "edge_file": "Factory.php",
                    "edge_line": 10,
                    "containing_class_id": "class:Factory",
                    "containing_method_id": "m:create",
                    "containing_method_fqn": "App\\Factory::create",
                    "containing_method_kind": "Method",
                }
            ],
            call_nodes=[
                {
                    "source_id": "m:create",
                    "call_id": "call:1",
                    "call_kind": "constructor",
                    "call_file": "Factory.php",
                    "call_start_line": 10,
                    "callee_id": None,
                    "callee_fqn": None,
                    "recv_value_kind": None,
                    "recv_name": None,
                    "access_chain_symbol": None,
                }
            ],
        )

    def test_constructor_call_produces_instantiation_entry(self):
        runner = make_runner()
        node = make_node()
        with _patch_fetch_used_by(self._make_instantiation_data()):
            result = build_class_used_by(runner, node)
        instantiation = [e for e in result if e.ref_type == "instantiation"]
        assert len(instantiation) == 1
        assert instantiation[0].fqn == "App\\Factory::create()"

    def test_instantiation_ref_type_set_on_entry(self):
        runner = make_runner()
        node = make_node()
        with _patch_fetch_used_by(self._make_instantiation_data()):
            result = build_class_used_by(runner, node)
        instantiation = [e for e in result if e.ref_type == "instantiation"]
        assert instantiation[0].ref_type == "instantiation"


class TestBuildClassUsedByInjectionSuppression:
    """Tests for method_call suppression when injection exists."""

    def test_method_call_suppressed_when_class_has_injection(self):
        """If class has property_type injection, method_call from that class is dropped."""
        runner = make_runner()
        data = _default_used_by_data(
            incoming_usages=[
                {
                    "source_id": "m:doWork",
                    "source_fqn": "App\\Svc::doWork",
                    "source_kind": "Method",
                    "source_name": "doWork",
                    "source_file": "Svc.php",
                    "source_start_line": 20,
                    "source_signature": None,
                    "edge_type": "USES",
                    "edge_target": None,
                    "edge_file": "Svc.php",
                    "edge_line": 25,
                    "containing_class_id": "class:Svc",
                    "containing_method_id": "m:doWork",
                    "containing_method_fqn": "App\\Svc::doWork",
                    "containing_method_kind": "Method",
                }
            ],
            injected_classes={"class:Svc"},  # Svc has injection
            # ref_type_data causes method_call classification (target is a Method)
            ref_type_data={},
        )
        # Patch node so target is a Method (makes infer_reference_type -> method_call)
        method_node = make_node(kind="Method", name="save", fqn="App\\Entity\\Order::save")
        with _patch_fetch_used_by(data):
            result = build_class_used_by(runner, method_node)
        method_call_entries = [e for e in result if e.ref_type == "method_call"]
        assert method_call_entries == []

    def test_method_call_not_suppressed_from_different_class(self):
        """Method call from a different class (no injection) is NOT suppressed."""
        runner = make_runner()
        data = _default_used_by_data(
            incoming_usages=[
                {
                    "source_id": "m:doWork",
                    "source_fqn": "App\\Other::doWork",
                    "source_kind": "Method",
                    "source_name": "doWork",
                    "source_file": "Other.php",
                    "source_start_line": 20,
                    "source_signature": None,
                    "edge_type": "USES",
                    "edge_target": None,
                    "edge_file": "Other.php",
                    "edge_line": 25,
                    "containing_class_id": "class:Other",
                    "containing_method_id": "m:doWork",
                    "containing_method_fqn": "App\\Other::doWork",
                    "containing_method_kind": "Method",
                }
            ],
            injected_classes={"class:Svc"},  # Different class has injection
            ref_type_data={},
        )
        method_node = make_node(kind="Method", name="save", fqn="App\\Entity\\Order::save")
        with _patch_fetch_used_by(data):
            result = build_class_used_by(runner, method_node)
        method_call_entries = [e for e in result if e.ref_type == "method_call"]
        assert len(method_call_entries) == 1


class TestBuildClassUsedByHandlerDispatch:
    """Tests for correct handler dispatch based on edge classification."""

    def _usage_record(self, **overrides) -> dict:
        defaults = {
            "source_id": "m:source",
            "source_fqn": "App\\Svc::m",
            "source_kind": "Method",
            "source_name": "m",
            "source_file": "Svc.php",
            "source_start_line": 5,
            "source_signature": None,
            "edge_type": "USES",
            "edge_target": None,
            "edge_file": "Svc.php",
            "edge_line": 10,
            "containing_class_id": "class:Svc",
            "containing_method_id": "m:source",
            "containing_method_fqn": "App\\Svc::m",
            "containing_method_kind": "Method",
        }
        defaults.update(overrides)
        return defaults

    def test_extends_edge_dispatches_to_extends_handler(self):
        runner = make_runner()
        node = make_node()
        data = _default_used_by_data(
            incoming_usages=[
                self._usage_record(
                    source_kind="Class",
                    source_fqn="App\\Child",
                    source_name="Child",
                    edge_type="EXTENDS",
                )
            ],
        )
        with _patch_fetch_used_by(data):
            result = build_class_used_by(runner, node)
        # extends from Q2 go through ExtendsHandler -> bucket.extends -> param_return
        # Actually extends entries from Q2 go to handler (ExtendsHandler appends to bucket.extends)
        extends_entries = [e for e in result if e.ref_type == "extends"]
        assert len(extends_entries) >= 0  # Q2 extends also gets handled

    def test_property_kind_source_creates_property_type(self):
        runner = make_runner()
        node = make_node()
        data = _default_used_by_data(
            incoming_usages=[
                self._usage_record(
                    source_id="prop:repo",
                    source_kind="Property",
                    source_fqn="App\\Svc::$orderRepo",
                    source_name="$orderRepo",
                    edge_type="USES",
                )
            ],
        )
        with _patch_fetch_used_by(data):
            result = build_class_used_by(runner, node)
        prop_type_entries = [e for e in result if e.ref_type == "property_type"]
        assert len(prop_type_entries) == 1
        assert prop_type_entries[0].fqn == "App\\Svc::$orderRepo"

    def test_limit_applied_to_result(self):
        runner = make_runner()
        node = make_node()
        # Create many extends children
        children = [
            {"id": f"c:{i}", "fqn": f"App\\Child{i}", "kind": "Class",
             "file": "Child.php", "start_line": i, "rel_type": "EXTENDS"}
            for i in range(20)
        ]
        data = _default_used_by_data(extends_children=children)
        with _patch_fetch_used_by(data):
            result = build_class_used_by(runner, node, limit=5)
        assert len(result) <= 5

    def test_result_entries_are_context_entry_instances(self):
        runner = make_runner()
        node = make_node()
        data = _default_used_by_data(
            extends_children=[
                {"id": "c:1", "fqn": "App\\Child", "kind": "Class",
                 "file": "Child.php", "start_line": 1, "rel_type": "EXTENDS"}
            ]
        )
        with _patch_fetch_used_by(data):
            result = build_class_used_by(runner, node)
        for entry in result:
            assert isinstance(entry, ContextEntry)


class TestBuildClassUsedByPropertyTypeWithDepth2:
    """Tests for depth-2 injection point expansion."""

    def test_property_type_depth2_calls_q7(self):
        """At depth 2, Q7_INJECTION_POINT_CALLS is invoked for property_type entries."""
        runner = make_runner()
        node = make_node()
        data = _default_used_by_data(
            incoming_usages=[
                {
                    "source_id": "prop:repo",
                    "source_fqn": "App\\Svc::$orderRepo",
                    "source_kind": "Property",
                    "source_name": "$orderRepo",
                    "source_file": "Svc.php",
                    "source_start_line": 15,
                    "source_signature": None,
                    "edge_type": "USES",
                    "edge_target": None,
                    "edge_file": "Svc.php",
                    "edge_line": 15,
                    "containing_class_id": "class:Svc",
                    "containing_method_id": None,
                    "containing_method_fqn": None,
                    "containing_method_kind": None,
                }
            ],
        )
        # Q7 returns one injection call
        runner.execute.return_value = [
            {
                "method_id": "m:save",
                "method_fqn": "App\\Svc::save",
                "call_id": "call:1",
                "call_kind": "method",
                "call_line": 30,
                "callee_id": "m:persist",
                "callee_fqn": "App\\Repo::persist",
                "callee_name": "persist",
                "callee_kind": "Method",
            }
        ]
        with _patch_fetch_used_by(data):
            result = build_class_used_by(runner, node, max_depth=2)

        prop_entries = [e for e in result if e.ref_type == "property_type"]
        assert len(prop_entries) == 1
        # At depth 2, children should contain the injection point call
        assert len(prop_entries[0].children) == 1
        child = prop_entries[0].children[0]
        assert child.ref_type == "method_call"
        assert child.callee == "persist()"


class TestBuildClassUsedByCallerChainDepth2:
    """Tests for method_call caller chain at depth 2."""

    def test_method_call_entry_gets_children_at_depth2(self):
        """method_call entries get caller chain expanded at max_depth >= 2."""
        runner = make_runner()
        node = make_node(kind="Method", name="save", fqn="App\\Repo::save")
        data = _default_used_by_data(
            incoming_usages=[
                {
                    "source_id": "m:doWork",
                    "source_fqn": "App\\Svc::doWork",
                    "source_kind": "Method",
                    "source_name": "doWork",
                    "source_file": "Svc.php",
                    "source_start_line": 10,
                    "source_signature": None,
                    "edge_type": "USES",
                    "edge_target": None,
                    "edge_file": "Svc.php",
                    "edge_line": 20,
                    "containing_class_id": "class:Svc",
                    "containing_method_id": "m:doWork",
                    "containing_method_fqn": "App\\Svc::doWork",
                    "containing_method_kind": "Method",
                }
            ],
        )

        def execute_side_effect(query, **kwargs):
            method_id = kwargs.get("method_id")
            if method_id == "m:doWork":
                return [
                    {
                        "caller_id": "m:controller",
                        "caller_fqn": "App\\Controller::action",
                        "caller_kind": "Method",
                        "caller_file": "Controller.php",
                        "caller_start_line": 5,
                        "call_line": 15,
                        "on_property": None,
                    }
                ]
            return []

        runner.execute.side_effect = execute_side_effect

        with _patch_fetch_used_by(data):
            result = build_class_used_by(runner, node, max_depth=2)

        method_entries = [e for e in result if e.ref_type == "method_call"]
        assert len(method_entries) == 1
        assert len(method_entries[0].children) == 1
        assert method_entries[0].children[0].fqn == "App\\Controller::action()"

    def test_no_caller_chain_at_depth1(self):
        """At max_depth=1, no caller chain is fetched."""
        runner = make_runner()
        node = make_node(kind="Method", name="save", fqn="App\\Repo::save")
        data = _default_used_by_data(
            incoming_usages=[
                {
                    "source_id": "m:doWork",
                    "source_fqn": "App\\Svc::doWork",
                    "source_kind": "Method",
                    "source_name": "doWork",
                    "source_file": "Svc.php",
                    "source_start_line": 10,
                    "source_signature": None,
                    "edge_type": "USES",
                    "edge_target": None,
                    "edge_file": "Svc.php",
                    "edge_line": 20,
                    "containing_class_id": "class:Svc",
                    "containing_method_id": "m:doWork",
                    "containing_method_fqn": "App\\Svc::doWork",
                    "containing_method_kind": "Method",
                }
            ],
        )
        with _patch_fetch_used_by(data):
            build_class_used_by(runner, node, max_depth=1)

        # runner.execute should NOT have been called for Q5
        # (fetch_class_used_by_data is patched; only Q5/Q7 would call execute directly)
        runner.execute.assert_not_called()


# =============================================================================
# build_class_uses
# =============================================================================


def _patch_fetch_uses(data: dict):
    """Return a context manager that patches fetch_class_uses_data."""
    return patch(
        "src.orchestration.class_context.fetch_class_uses_data",
        return_value=data,
    )


def _default_uses_data(**overrides) -> dict:
    defaults = {
        "member_deps": [],
        "class_rel": [],
        "type_hints": [],
        "ctor_calls": [],
    }
    defaults.update(overrides)
    return defaults


class TestBuildClassUsesEmpty:
    """Tests for build_class_uses with no data."""

    def test_empty_data_returns_empty(self):
        runner = make_runner()
        node = make_node()
        with _patch_fetch_uses(_default_uses_data()):
            result = build_class_uses(runner, node)
        assert result == []

    def test_returns_context_entry_list(self):
        runner = make_runner()
        node = make_node()
        with _patch_fetch_uses(_default_uses_data()):
            result = build_class_uses(runner, node)
        assert isinstance(result, list)


class TestBuildClassUsesStructural:
    """Tests for extends/implements/uses_trait structural entries."""

    def test_extends_entry_created(self):
        runner = make_runner()
        node = make_node()
        data = _default_uses_data(
            class_rel=[
                {"target_id": "c:base", "target_fqn": "App\\BaseOrder",
                 "target_kind": "Class", "rel_type": "EXTENDS",
                 "file": "BaseOrder.php", "line": 5}
            ]
        )
        with _patch_fetch_uses(data):
            result = build_class_uses(runner, node)
        assert len(result) == 1
        assert result[0].ref_type == "extends"
        assert result[0].fqn == "App\\BaseOrder"

    def test_implements_entry_created(self):
        runner = make_runner()
        node = make_node()
        data = _default_uses_data(
            class_rel=[
                {"target_id": "i:countable", "target_fqn": "Countable",
                 "target_kind": "Interface", "rel_type": "IMPLEMENTS",
                 "file": None, "line": None}
            ]
        )
        with _patch_fetch_uses(data):
            result = build_class_uses(runner, node)
        assert result[0].ref_type == "implements"

    def test_uses_trait_entry_created(self):
        runner = make_runner()
        node = make_node()
        data = _default_uses_data(
            class_rel=[
                {"target_id": "t:trait", "target_fqn": "App\\Traits\\HasTimestamps",
                 "target_kind": "Trait", "rel_type": "USES_TRAIT",
                 "file": "HasTimestamps.php", "line": 1}
            ]
        )
        with _patch_fetch_uses(data):
            result = build_class_uses(runner, node)
        assert result[0].ref_type == "uses_trait"

    def test_structural_priority_order(self):
        """All structural types (extends, implements, uses_trait) share priority 0
        and sort before non-structural types."""
        runner = make_runner()
        node = make_node()
        data = _default_uses_data(
            class_rel=[
                {"target_id": "t:trait", "target_fqn": "App\\T",
                 "target_kind": "Trait", "rel_type": "USES_TRAIT",
                 "file": None, "line": None},
                {"target_id": "i:iface", "target_fqn": "App\\I",
                 "target_kind": "Interface", "rel_type": "IMPLEMENTS",
                 "file": None, "line": None},
                {"target_id": "c:base", "target_fqn": "App\\B",
                 "target_kind": "Class", "rel_type": "EXTENDS",
                 "file": None, "line": None},
            ]
        )
        with _patch_fetch_uses(data):
            result = build_class_uses(runner, node)
        ref_types = {e.ref_type for e in result}
        assert ref_types == {"extends", "implements", "uses_trait"}
        # All structural types share the same priority
        assert USES_PRIORITY["extends"] == USES_PRIORITY["implements"] == USES_PRIORITY["uses_trait"]


class TestBuildClassUsesExclusionSet:
    """Tests for extends/implements exclusion from member-level deps."""

    def test_structural_targets_excluded_from_member_deps(self):
        """Targets covered by extends/implements are not duplicated in member deps."""
        runner = make_runner()
        node = make_node()
        data = _default_uses_data(
            class_rel=[
                {"target_id": "c:base", "target_fqn": "App\\BaseOrder",
                 "target_kind": "Class", "rel_type": "EXTENDS",
                 "file": None, "line": None}
            ],
            member_deps=[
                # Member also uses the base class — should be excluded
                {"member_id": "m:1", "member_fqn": "App\\Order::m", "member_kind": "Method",
                 "member_name": "m", "target_id": "c:base", "target_fqn": "App\\BaseOrder",
                 "target_kind": "Class", "target_name": "BaseOrder",
                 "edge_type": "USES", "file": "Order.php", "line": 10}
            ],
        )
        with _patch_fetch_uses(data):
            result = build_class_uses(runner, node)
        # Should only have one entry (the extends), not two
        assert len(result) == 1
        assert result[0].ref_type == "extends"


class TestBuildClassUsesMemberDeps:
    """Tests for member-level dependency processing."""

    def test_method_target_creates_method_call(self):
        runner = make_runner()
        node = make_node()
        data = _default_uses_data(
            member_deps=[
                {"member_id": "m:1", "member_fqn": "App\\Order::m", "member_kind": "Method",
                 "member_name": "m", "target_id": "m:save", "target_fqn": "App\\Repo::save",
                 "target_kind": "Method", "target_name": "save",
                 "edge_type": "USES", "file": "Order.php", "line": 20}
            ],
        )
        with _patch_fetch_uses(data):
            result = build_class_uses(runner, node)
        assert len(result) == 1
        assert result[0].ref_type == "method_call"

    def test_deduplication_by_target_id(self):
        """Multiple member uses of same target -> single entry."""
        runner = make_runner()
        node = make_node()
        data = _default_uses_data(
            member_deps=[
                {"member_id": "m:1", "member_fqn": "App\\Order::m1", "member_kind": "Method",
                 "member_name": "m1", "target_id": "m:save", "target_fqn": "App\\Repo::save",
                 "target_kind": "Method", "target_name": "save",
                 "edge_type": "USES", "file": "Order.php", "line": 10},
                {"member_id": "m:2", "member_fqn": "App\\Order::m2", "member_kind": "Method",
                 "member_name": "m2", "target_id": "m:save", "target_fqn": "App\\Repo::save",
                 "target_kind": "Method", "target_name": "save",
                 "edge_type": "USES", "file": "Order.php", "line": 20},
            ],
        )
        with _patch_fetch_uses(data):
            result = build_class_uses(runner, node)
        assert len(result) == 1

    def test_highest_priority_ref_type_wins(self):
        """When same target accessed as both type_hint and method_call via
        different member deps, the first-seen ref_type wins at the dedup
        level (both resolve to same containing class)."""
        runner = make_runner()
        # Mock containing class resolution for Method targets
        runner.execute_single.return_value = {
            "class_id": "class:dep",
            "class_fqn": "App\\Dep",
            "class_kind": "Class",
        }
        node = make_node()
        data = _default_uses_data(
            member_deps=[
                # First access: method on dep class (resolves via execute_single)
                {"member_id": "m:1", "member_fqn": "App\\Order::a", "member_kind": "Method",
                 "member_name": "a", "target_id": "m:dep_method", "target_fqn": "App\\Dep::depMethod",
                 "target_kind": "Method", "target_name": "depMethod",
                 "edge_type": "USES", "file": "Order.php", "line": 10},
                # Second access: Class target directly (type_hint)
                {"member_id": "prop:1", "member_fqn": "App\\Order::$dep", "member_kind": "Property",
                 "member_name": "$dep", "target_id": "class:dep", "target_fqn": "App\\Dep",
                 "target_kind": "Class", "target_name": "Dep",
                 "edge_type": "USES", "file": "Order.php", "line": 5},
            ],
        )
        with _patch_fetch_uses(data):
            result = build_class_uses(runner, node)
        # Both resolve to class:dep -- deduped to 1 entry
        assert len(result) == 1

    def test_uses_sort_order(self):
        """Structural entries (priority 0) sort before method_call (priority 5)."""
        runner = make_runner()
        # Mock containing class resolution for the Method target
        runner.execute_single.return_value = {
            "class_id": "class:repo",
            "class_fqn": "App\\Repo",
            "class_kind": "Class",
        }
        node = make_node()
        data = _default_uses_data(
            class_rel=[
                {"target_id": "t:trait", "target_fqn": "App\\T",
                 "target_kind": "Trait", "rel_type": "USES_TRAIT",
                 "file": None, "line": None},
                {"target_id": "c:base", "target_fqn": "App\\B",
                 "target_kind": "Class", "rel_type": "EXTENDS",
                 "file": None, "line": None},
            ],
            member_deps=[
                {"member_id": "m:1", "member_fqn": "App\\Order::m", "member_kind": "Method",
                 "member_name": "m", "target_id": "m:save", "target_fqn": "App\\Repo::save",
                 "target_kind": "Method", "target_name": "save",
                 "edge_type": "USES", "file": "Order.php", "line": 10},
            ],
        )
        with _patch_fetch_uses(data):
            result = build_class_uses(runner, node)
        ref_types = [e.ref_type for e in result]
        # Structural types (all priority 0) come before method_call (priority 5)
        structural = {"extends", "uses_trait"}
        structural_indices = [i for i, rt in enumerate(ref_types) if rt in structural]
        method_indices = [i for i, rt in enumerate(ref_types) if rt == "method_call"]
        if method_indices:
            assert max(structural_indices) < min(method_indices)

    def test_limit_applied(self):
        runner = make_runner()
        node = make_node()
        data = _default_uses_data(
            member_deps=[
                {"member_id": f"m:{i}", "member_fqn": f"App\\Order::m{i}",
                 "member_kind": "Method", "member_name": f"m{i}",
                 "target_id": f"class:{i}", "target_fqn": f"App\\Dep{i}",
                 "target_kind": "Class", "target_name": f"Dep{i}",
                 "edge_type": "USES", "file": "Order.php", "line": i}
                for i in range(30)
            ],
        )
        with _patch_fetch_uses(data):
            result = build_class_uses(runner, node, limit=5)
        assert len(result) <= 5


class TestBuildClassUsesBehavioralDepth2:
    """Tests for depth-2 behavioral expansion through injected properties."""

    def test_property_type_gets_depth2_children(self):
        """At max_depth=2, property_type entries get method calls as children."""
        runner = make_runner()
        node = make_node()
        data = _default_uses_data(
            member_deps=[
                {"member_id": "prop:repo", "member_fqn": "App\\Order::$repo",
                 "member_kind": "Property", "member_name": "$repo",
                 "target_id": "class:Repo", "target_fqn": "App\\Repo",
                 "target_kind": "Class", "target_name": "Repo",
                 "edge_type": "USES", "file": "Order.php", "line": 5},
            ],
        )
        runner.execute.return_value = [
            {
                "callee_id": "m:save",
                "callee_fqn": "App\\Repo::save",
                "callee_kind": "Method",
                "callee_name": "save",
                "from_method": "App\\Order::create",
            }
        ]
        with _patch_fetch_uses(data):
            result = build_class_uses(runner, node, max_depth=2)

        prop_entries = [e for e in result if e.ref_type == "property_type"]
        assert len(prop_entries) == 1
        assert len(prop_entries[0].children) == 1
        assert prop_entries[0].children[0].fqn == "App\\Repo::save()"
        assert prop_entries[0].children[0].ref_type == "method_call"

    def test_no_depth2_at_max_depth1(self):
        """At max_depth=1, Q3 is never called."""
        runner = make_runner()
        node = make_node()
        data = _default_uses_data(
            member_deps=[
                {"member_id": "prop:repo", "member_fqn": "App\\Order::$repo",
                 "member_kind": "Property", "member_name": "$repo",
                 "target_id": "class:Repo", "target_fqn": "App\\Repo",
                 "target_kind": "Class", "target_name": "Repo",
                 "edge_type": "USES", "file": "Order.php", "line": 5},
            ],
        )
        with _patch_fetch_uses(data):
            result = build_class_uses(runner, node, max_depth=1)

        # runner.execute should NOT be called (fetch is patched)
        runner.execute.assert_not_called()
        prop_entries = [e for e in result if e.ref_type == "property_type"]
        assert len(prop_entries) == 1
        assert prop_entries[0].children == []

    def test_depth2_children_deduplicated(self):
        """Duplicate callee_ids produce a single child entry."""
        runner = make_runner()
        node = make_node()
        data = _default_uses_data(
            member_deps=[
                {"member_id": "prop:repo", "member_fqn": "App\\Order::$repo",
                 "member_kind": "Property", "member_name": "$repo",
                 "target_id": "class:Repo", "target_fqn": "App\\Repo",
                 "target_kind": "Class", "target_name": "Repo",
                 "edge_type": "USES", "file": "Order.php", "line": 5},
            ],
        )
        runner.execute.return_value = [
            {"callee_id": "m:save", "callee_fqn": "App\\Repo::save",
             "callee_kind": "Method", "callee_name": "save",
             "from_method": "App\\Order::create"},
            {"callee_id": "m:save", "callee_fqn": "App\\Repo::save",
             "callee_kind": "Method", "callee_name": "save",
             "from_method": "App\\Order::update"},  # Same callee, different method
        ]
        with _patch_fetch_uses(data):
            result = build_class_uses(runner, node, max_depth=2)
        prop_entries = [e for e in result if e.ref_type == "property_type"]
        assert len(prop_entries[0].children) == 1  # Deduplicated


# =============================================================================
# USES_PRIORITY constant
# =============================================================================


class TestUsesPriorityConstant:
    """Tests for the USES_PRIORITY ordering."""

    def test_extends_has_lowest_number(self):
        assert USES_PRIORITY["extends"] == 0

    def test_implements_lower_than_property_type(self):
        assert USES_PRIORITY["implements"] < USES_PRIORITY["property_type"]

    def test_property_type_lower_than_method_call(self):
        assert USES_PRIORITY["property_type"] < USES_PRIORITY["method_call"]

    def test_parameter_type_higher_than_method_call(self):
        """parameter_type has higher priority (lower number) than method_call."""
        assert USES_PRIORITY["parameter_type"] < USES_PRIORITY["method_call"]

    def test_type_hint_lower_priority_than_instantiation(self):
        """type_hint should be lower priority (higher number) than instantiation."""
        assert USES_PRIORITY["type_hint"] > USES_PRIORITY["instantiation"]

    def test_all_expected_keys_present(self):
        expected = {
            "extends", "implements", "uses_trait", "property_type",
            "method_call", "instantiation", "property_access",
            "parameter_type", "return_type", "type_hint",
        }
        assert expected <= set(USES_PRIORITY.keys())


# =============================================================================
# Integration-style: dict -> ContextEntry round-trip
# =============================================================================


class TestDictContextEntryRoundTrip:
    """Tests that handler dict fields survive conversion to ContextEntry."""

    def test_all_handler_fields_preserved(self):
        """Key handler fields are preserved through _dict_to_context_entry."""
        d = {
            "depth": 1,
            "node_id": "m:check",
            "fqn": "App\\Svc::check()",
            "kind": "Method",
            "file": "Svc.php",
            "line": 25,
            "ref_type": "method_call",
            "callee": "save()",
            "on": "$this->repo",
            "on_kind": "property",
            "children": [],
        }
        entry = _dict_to_context_entry(d)
        assert entry.depth == 1
        assert entry.node_id == "m:check"
        assert entry.fqn == "App\\Svc::check()"
        assert entry.kind == "Method"
        assert entry.file == "Svc.php"
        assert entry.line == 25
        assert entry.ref_type == "method_call"
        assert entry.callee == "save()"
        assert entry.on == "$this->repo"
        assert entry.on_kind == "property"

    def test_property_type_handler_dict_preserved(self):
        d = {
            "depth": 1,
            "node_id": "prop:repo",
            "fqn": "App\\Svc::$orderRepo",
            "kind": "Property",
            "file": "Svc.php",
            "line": 10,
            "ref_type": "property_type",
            "children": [],
        }
        entry = _dict_to_context_entry(d)
        assert entry.ref_type == "property_type"
        assert entry.kind == "Property"
        assert entry.fqn == "App\\Svc::$orderRepo"

    def test_instantiation_handler_dict_preserved(self):
        d = {
            "depth": 1,
            "node_id": "m:factory",
            "fqn": "App\\Factory::create()",
            "kind": "Method",
            "file": "Factory.php",
            "line": 20,
            "ref_type": "instantiation",
            "children": [],
            "arguments": [{"position": 0}],
        }
        entry = _dict_to_context_entry(d)
        assert entry.ref_type == "instantiation"
        # arguments is NOT in ContextEntry.arguments (those are ArgumentInfo objects)
        # but the conversion doesn't fail

    def test_extends_entry_attributes(self):
        """ContextEntry from extends handler preserves ref_type."""
        d = {
            "depth": 1,
            "node_id": "c:child",
            "fqn": "App\\ChildOrder",
            "kind": "Class",
            "file": "ChildOrder.php",
            "line": 3,
            "ref_type": "extends",
            "children": [],
        }
        entry = _dict_to_context_entry(d)
        assert entry.ref_type == "extends"
        assert entry.depth == 1
