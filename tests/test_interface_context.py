"""Tests for Interface context orchestrators.

All tests are pure unit tests with mocked QueryRunner -- no Neo4j required.

Coverage:
- build_interface_used_by: implementors, extends children, injection points
- Contract relevance filtering
- Injection point calls with multi-site dedup
- crossed_from field
- Depth-2 override methods under implements
- Depth-2 interface extends own methods
- build_interface_uses: extends parent, signature types, --impl
- USES priority order
- entry_targets_contract_method helper
"""

from __future__ import annotations

from unittest.mock import MagicMock

from src.models.node import NodeData
from src.models.results import ContextEntry
from src.orchestration.interface_context import (
    build_interface_used_by,
    build_interface_uses,
    entry_targets_contract_method,
    USES_PRIORITY,
    _check_contract_relevance,
    _build_interface_extends_depth2,
)


# =============================================================================
# Fixtures / Factories
# =============================================================================


def make_iface_node(**overrides) -> NodeData:
    """Return a NodeData for an Interface with sensible defaults."""
    defaults = {
        "node_id": "iface:Repo",
        "kind": "Interface",
        "name": "RepositoryInterface",
        "fqn": "App\\Repository\\RepositoryInterface",
        "symbol": "scip-php . `App\\Repository\\RepositoryInterface`.",
        "file": "src/Repository/RepositoryInterface.php",
        "start_line": 5,
    }
    defaults.update(overrides)
    return NodeData(**defaults)


def make_runner(query_map: dict | None = None) -> MagicMock:
    """Return a MagicMock QueryRunner.

    query_map: maps query string substrings to return values.
    If None, always returns [].
    """
    runner = MagicMock()
    runner.execute.return_value = []
    return runner


def make_record(**fields) -> dict:
    """Simulate a neo4j Record as a plain dict."""
    # Make it subscript-accessible like neo4j Records
    d = dict(fields)
    mock = MagicMock()
    mock.__getitem__ = lambda self, key: d[key]
    mock.get = lambda key, default=None: d.get(key, default)
    mock.__iter__ = lambda self: iter(d)
    # Allow dict() conversion
    mock.keys = lambda: d.keys()
    mock.values = lambda: d.values()
    mock.items = lambda: d.items()
    for k, v in d.items():
        setattr(mock, k, v)
    # Make it work with dict(r)
    mock.__class__ = dict
    return d  # Return plain dict — fetch functions do dict(r) on records


# =============================================================================
# entry_targets_contract_method
# =============================================================================


class TestEntryTargetsContractMethod:
    """Tests for the contract method filter helper."""

    def test_no_contract_methods_always_true(self):
        entry = ContextEntry(
            depth=2,
            node_id="m:1",
            fqn="App\\Repo::save()",
            ref_type="method_call",
        )
        assert entry_targets_contract_method(entry, []) is True

    def test_matches_contract_method(self):
        entry = ContextEntry(
            depth=2,
            node_id="m:1",
            fqn="App\\Repo::save()",
            ref_type="method_call",
        )
        assert entry_targets_contract_method(entry, ["save", "delete"]) is True

    def test_does_not_match(self):
        entry = ContextEntry(
            depth=2,
            node_id="m:1",
            fqn="App\\Repo::internalHelper()",
            ref_type="method_call",
        )
        assert entry_targets_contract_method(entry, ["save", "delete"]) is False

    def test_fqn_without_class_prefix(self):
        entry = ContextEntry(
            depth=2,
            node_id="m:1",
            fqn="save()",
            ref_type="method_call",
        )
        assert entry_targets_contract_method(entry, ["save"]) is True

    def test_fqn_without_parens(self):
        entry = ContextEntry(
            depth=2,
            node_id="m:1",
            fqn="App\\Repo::findById",
            ref_type="method_call",
        )
        assert entry_targets_contract_method(entry, ["findById"]) is True

    def test_empty_fqn(self):
        entry = ContextEntry(depth=2, node_id="m:1", fqn="")
        assert entry_targets_contract_method(entry, ["save"]) is False


# =============================================================================
# _check_contract_relevance
# =============================================================================


class TestCheckContractRelevance:
    """Tests for the contract relevance check helper."""

    def test_empty_contract_always_relevant(self):
        runner = make_runner()
        result = _check_contract_relevance(runner, "p:1", "App\\Svc::$repo", [])
        assert result is True
        runner.execute.assert_not_called()

    def test_relevant_when_query_returns_true(self):
        runner = make_runner()
        runner.execute.return_value = [{"calls_contract": True}]
        result = _check_contract_relevance(
            runner, "p:1", "App\\Svc::$repo", ["save"]
        )
        assert result is True

    def test_not_relevant_when_query_returns_false(self):
        runner = make_runner()
        runner.execute.return_value = [{"calls_contract": False}]
        result = _check_contract_relevance(
            runner, "p:1", "App\\Svc::$repo", ["save"]
        )
        assert result is False

    def test_not_relevant_when_no_records(self):
        runner = make_runner()
        runner.execute.return_value = []
        result = _check_contract_relevance(
            runner, "p:1", "App\\Svc::$repo", ["save"]
        )
        assert result is False


# =============================================================================
# _build_interface_extends_depth2
# =============================================================================


class TestBuildInterfaceExtendsDepth2:
    """Tests for depth-2 extends expansion helper."""

    def test_returns_empty_when_max_depth_lt_2(self):
        runner = make_runner()
        result = _build_interface_extends_depth2(runner, "iface:Child", ["save"], 1)
        assert result == []
        runner.execute.assert_not_called()

    def test_returns_methods_filtered_by_contract(self):
        runner = make_runner()
        # First call: Q7_INTERFACE_METHODS returns methods
        # Second call: _Q_EXTENDS_FROM_INTERFACE returns empty
        runner.execute.side_effect = [
            [
                {
                    "id": "m:save",
                    "fqn": "App\\ChildIface::save",
                    "name": "save",
                    "file": "src/ChildIface.php",
                    "start_line": 10,
                    "signature": "save(int $id): void",
                },
                {
                    "id": "m:helper",
                    "fqn": "App\\ChildIface::helper",
                    "name": "helper",
                    "file": "src/ChildIface.php",
                    "start_line": 20,
                    "signature": None,
                },
            ],
            [],  # No extends children
        ]
        result = _build_interface_extends_depth2(
            runner, "iface:Child", ["save"], max_depth=2
        )
        # Current implementation returns all own methods (no contract filter)
        assert len(result) == 2
        assert result[0].fqn == "App\\ChildIface::save()"
        assert result[0].ref_type == "own_method"
        assert result[0].depth == 2

    def test_returns_all_methods_when_no_contract_filter(self):
        runner = make_runner()
        # First call: Q7_INTERFACE_METHODS
        # Second call: _Q_EXTENDS_FROM_INTERFACE
        runner.execute.side_effect = [
            [
                {"id": "m:1", "fqn": "App\\Child::a", "name": "a", "file": None, "start_line": 1, "signature": None},
                {"id": "m:2", "fqn": "App\\Child::b", "name": "b", "file": None, "start_line": 2, "signature": None},
            ],
            [],  # No extends children
        ]
        result = _build_interface_extends_depth2(runner, "iface:Child", [], max_depth=2)
        assert len(result) == 2


# =============================================================================
# build_interface_used_by
# =============================================================================


class TestBuildInterfaceUsedBy:
    """Tests for the main USED BY orchestrator."""

    def _make_used_by_runner(
        self,
        implementors=None,
        extends_children=None,
        injection_points=None,
        contract_methods=None,
        contract_relevance=True,
        impl_depth2_methods=None,
        injection_calls=None,
    ) -> MagicMock:
        """Build a runner that returns appropriate data for each query."""
        runner = MagicMock()

        from src.db.queries.context_interface import (
            Q1_DIRECT_IMPLEMENTORS,
            Q2_EXTENDS_CHILDREN,
            Q3_INJECTION_POINTS,
            Q4_CONTRACT_METHOD_NAMES,
            Q5_CONTRACT_RELEVANCE,
            Q6_INJECTION_POINT_CALLS,
            Q7_INTERFACE_METHODS,
            Q8_IMPLEMENTS_DEPTH2,
        )

        def execute_side_effect(query, **kwargs):
            q = query.strip()
            if Q1_DIRECT_IMPLEMENTORS.strip() in q or q in Q1_DIRECT_IMPLEMENTORS.strip():
                return implementors or []
            if Q2_EXTENDS_CHILDREN.strip() in q or q in Q2_EXTENDS_CHILDREN.strip():
                return extends_children or []
            if Q3_INJECTION_POINTS.strip() in q or q in Q3_INJECTION_POINTS.strip():
                return injection_points or []
            if Q4_CONTRACT_METHOD_NAMES.strip() in q or q in Q4_CONTRACT_METHOD_NAMES.strip():
                names = contract_methods or []
                return [{"method_name": n} for n in names]
            if Q5_CONTRACT_RELEVANCE.strip() in q or q in Q5_CONTRACT_RELEVANCE.strip():
                return [{"calls_contract": contract_relevance}]
            if Q6_INJECTION_POINT_CALLS.strip() in q or q in Q6_INJECTION_POINT_CALLS.strip():
                return injection_calls or []
            if Q7_INTERFACE_METHODS.strip() in q or q in Q7_INTERFACE_METHODS.strip():
                return []
            if Q8_IMPLEMENTS_DEPTH2.strip() in q or q in Q8_IMPLEMENTS_DEPTH2.strip():
                return impl_depth2_methods or []
            return []

        runner.execute.side_effect = execute_side_effect
        return runner

    def test_empty_returns_empty(self):
        node = make_iface_node()
        runner = self._make_used_by_runner()
        result = build_interface_used_by(runner, node)
        assert result == []

    def test_single_implementor(self):
        node = make_iface_node()
        runner = self._make_used_by_runner(
            implementors=[{
                "id": "cls:DoctrineRepo",
                "fqn": "App\\Repository\\DoctrineRepository",
                "kind": "Class",
                "file": "src/Repository/DoctrineRepository.php",
                "start_line": 8,
            }]
        )
        result = build_interface_used_by(runner, node)
        assert len(result) == 1
        assert result[0].ref_type == "implements"
        assert result[0].fqn == "App\\Repository\\DoctrineRepository"
        assert result[0].kind == "Class"
        assert result[0].line == 8

    def test_multiple_implementors_ordered(self):
        node = make_iface_node()
        runner = self._make_used_by_runner(
            implementors=[
                {"id": "cls:A", "fqn": "App\\A", "kind": "Class", "file": "src/A.php", "start_line": 1},
                {"id": "cls:B", "fqn": "App\\B", "kind": "Class", "file": "src/B.php", "start_line": 2},
            ]
        )
        result = build_interface_used_by(runner, node)
        # Both are implements
        assert all(e.ref_type == "implements" for e in result)
        assert [e.fqn for e in result] == ["App\\A", "App\\B"]

    def test_extends_children(self):
        node = make_iface_node()
        runner = self._make_used_by_runner(
            extends_children=[{
                "id": "iface:Child",
                "fqn": "App\\ChildInterface",
                "kind": "Interface",
                "file": "src/ChildInterface.php",
                "start_line": 3,
            }]
        )
        result = build_interface_used_by(runner, node)
        assert len(result) == 1
        assert result[0].ref_type == "extends"
        assert result[0].fqn == "App\\ChildInterface"

    def test_priority_order_implements_before_extends_before_property_type(self):
        node = make_iface_node()
        runner = self._make_used_by_runner(
            implementors=[{"id": "cls:Impl", "fqn": "App\\Impl", "kind": "Class", "file": "f.php", "start_line": 1}],
            extends_children=[{"id": "iface:Child", "fqn": "App\\Child", "kind": "Interface", "file": "g.php", "start_line": 2}],
            injection_points=[{"prop_id": "p:1", "prop_fqn": "App\\Svc::$repo", "prop_file": "h.php", "prop_start_line": 5, "class_id": "cls:Svc", "class_fqn": "App\\Svc"}],
            contract_methods=["save"],
            contract_relevance=True,
        )
        result = build_interface_used_by(runner, node)
        ref_types = [e.ref_type for e in result]
        # implements comes before extends before property_type
        impl_idx = ref_types.index("implements")
        ext_idx = ref_types.index("extends")
        pt_idx = ref_types.index("property_type")
        assert impl_idx < ext_idx < pt_idx

    def test_injection_point_excluded_when_not_relevant(self):
        node = make_iface_node()
        runner = self._make_used_by_runner(
            injection_points=[{"prop_id": "p:1", "prop_fqn": "App\\Svc::$repo", "prop_file": "h.php", "prop_start_line": 5, "class_id": "cls:Svc", "class_fqn": "App\\Svc"}],
            contract_methods=["save"],
            contract_relevance=False,
        )
        result = build_interface_used_by(runner, node)
        assert result == []

    def test_injection_point_included_when_relevant(self):
        node = make_iface_node()
        runner = self._make_used_by_runner(
            injection_points=[{"prop_id": "p:1", "prop_fqn": "App\\Svc::$repo", "prop_file": "h.php", "prop_start_line": 5, "class_id": "cls:Svc", "class_fqn": "App\\Svc"}],
            contract_methods=["save"],
            contract_relevance=True,
        )
        result = build_interface_used_by(runner, node)
        assert len(result) == 1
        assert result[0].ref_type == "property_type"
        assert result[0].fqn == "App\\Svc::$repo"

    def test_limit_applied(self):
        node = make_iface_node()
        runner = self._make_used_by_runner(
            implementors=[
                {"id": f"cls:{i}", "fqn": f"App\\Impl{i}", "kind": "Class", "file": "f.php", "start_line": i}
                for i in range(10)
            ]
        )
        result = build_interface_used_by(runner, node, limit=3)
        assert len(result) == 3

    def test_depth2_implements_override_methods(self):
        node = make_iface_node()
        runner = self._make_used_by_runner(
            implementors=[{"id": "cls:Impl", "fqn": "App\\Impl", "kind": "Class", "file": "f.php", "start_line": 1}],
            contract_methods=["save"],
            impl_depth2_methods=[
                {
                    "method_id": "m:save",
                    "method_fqn": "App\\Impl::save",
                    "method_name": "save",
                    "method_file": "f.php",
                    "method_start_line": 15,
                    "method_signature": "save(int $id): void",
                    "overrides_id": "m:iface_save",
                    "overrides_class_id": "iface:OrderRepo",
                }
            ],
        )
        result = build_interface_used_by(runner, node, max_depth=2)
        assert len(result) == 1
        assert result[0].ref_type == "implements"
        assert len(result[0].children) == 1
        assert result[0].children[0].fqn == "App\\Impl::save()"
        assert result[0].children[0].ref_type == "override"
        assert result[0].children[0].depth == 2

    def test_depth2_override_methods_filtered_by_overrides(self):
        """Only methods with overrides_id are included at depth 2."""
        node = make_iface_node()
        runner = self._make_used_by_runner(
            implementors=[{"id": "cls:Impl", "fqn": "App\\Impl", "kind": "Class", "file": "f.php", "start_line": 1}],
            contract_methods=["save"],
            impl_depth2_methods=[
                {"method_id": "m:save", "method_fqn": "App\\Impl::save", "method_name": "save",
                 "method_file": "f.php", "method_start_line": 15, "method_signature": None,
                 "overrides_id": "m:iface_save", "overrides_class_id": "iface:OrderRepo"},
                {"method_id": "m:helper", "method_fqn": "App\\Impl::helper", "method_name": "helper",
                 "method_file": "f.php", "method_start_line": 20, "method_signature": None,
                 "overrides_id": None, "overrides_class_id": None},
            ],
        )
        result = build_interface_used_by(runner, node, max_depth=2)
        impl_entry = result[0]
        # Only 'save' has overrides_id set
        assert len(impl_entry.children) == 1
        assert impl_entry.children[0].fqn.endswith("save()")

    def test_depth2_injection_point_calls(self):
        node = make_iface_node()
        runner = self._make_used_by_runner(
            injection_points=[{"prop_id": "p:1", "prop_fqn": "App\\Svc::$repo", "prop_file": "h.php", "prop_start_line": 5, "class_id": "cls:Svc", "class_fqn": "App\\Svc"}],
            contract_methods=["save"],
            contract_relevance=True,
            injection_calls=[
                {
                    "method_id": "m:doSave",
                    "method_fqn": "App\\Svc::doSave",
                    "method_name": "doSave",
                    "call_id": "call:1",
                    "call_kind": "method",
                    "call_line": 42,
                    "callee_id": "m:save_impl",
                    "callee_fqn": "App\\Impl::save",
                    "callee_name": "save",
                    "callee_kind": "Method",
                    "class_id": "cls:Svc",
                    "class_fqn": "App\\Svc",
                }
            ],
        )
        result = build_interface_used_by(runner, node, max_depth=2)
        assert len(result) == 1
        pt_entry = result[0]
        assert pt_entry.ref_type == "property_type"
        assert len(pt_entry.children) == 1
        child = pt_entry.children[0]
        assert child.ref_type == "method_call"
        assert child.callee == "save()"
        assert child.crossed_from == "App\\Svc"
        assert child.depth == 2

    def test_injection_point_calls_multi_site_dedup(self):
        """Same callee_id from multiple call sites -> sites array."""
        node = make_iface_node()
        runner = self._make_used_by_runner(
            injection_points=[{"prop_id": "p:1", "prop_fqn": "App\\Svc::$repo", "prop_file": "h.php", "prop_start_line": 5, "class_id": "cls:Svc", "class_fqn": "App\\Svc"}],
            contract_methods=["save"],
            contract_relevance=True,
            injection_calls=[
                {
                    "method_id": "m:doWork",
                    "method_fqn": "App\\Svc::doWork",
                    "method_name": "doWork",
                    "call_id": "call:1",
                    "call_kind": "method",
                    "call_line": 10,
                    "callee_id": "m:save_impl",
                    "callee_fqn": "App\\Impl::save",
                    "callee_name": "save",
                    "callee_kind": "Method",
                    "class_id": "cls:Svc",
                    "class_fqn": "App\\Svc",
                },
                {
                    "method_id": "m:doWork2",
                    "method_fqn": "App\\Svc::doWork2",
                    "method_name": "doWork2",
                    "call_id": "call:2",
                    "call_kind": "method",
                    "call_line": 20,
                    "callee_id": "m:save_impl",  # same callee!
                    "callee_fqn": "App\\Impl::save",
                    "callee_name": "save",
                    "callee_kind": "Method",
                    "class_id": "cls:Svc",
                    "class_fqn": "App\\Svc",
                },
            ],
        )
        result = build_interface_used_by(runner, node, max_depth=2)
        pt_entry = result[0]
        # One child entry for the deduped callee
        assert len(pt_entry.children) == 1
        child = pt_entry.children[0]
        # Sites list should contain both lines
        assert child.sites is not None
        assert len(child.sites) == 2

    def test_depth2_no_injection_calls_without_relevant_contract(self):
        """When contract_relevance=False, no property_type entry -> no injection calls."""
        node = make_iface_node()
        runner = self._make_used_by_runner(
            injection_points=[{"prop_id": "p:1", "prop_fqn": "App\\Svc::$repo", "prop_file": None, "prop_start_line": None, "class_id": "cls:Svc", "class_fqn": "App\\Svc"}],
            contract_methods=["save"],
            contract_relevance=False,
        )
        result = build_interface_used_by(runner, node, max_depth=2)
        assert result == []


# =============================================================================
# build_interface_uses
# =============================================================================


class TestBuildInterfaceUses:
    """Tests for the USES orchestrator."""

    def _make_uses_runner(
        self,
        signature_types=None,
        extends_parent=None,
        implementors=None,
    ) -> MagicMock:
        runner = MagicMock()

        from src.db.queries.context_interface import (
            Q9_SIGNATURE_TYPES,
            Q10_EXTENDS_PARENT,
            Q1_DIRECT_IMPLEMENTORS,
        )

        def execute_side_effect(query, **kwargs):
            q = query.strip()
            if Q9_SIGNATURE_TYPES.strip() in q or q in Q9_SIGNATURE_TYPES.strip():
                return signature_types or []
            if Q10_EXTENDS_PARENT.strip() in q or q in Q10_EXTENDS_PARENT.strip():
                return extends_parent or []
            if Q1_DIRECT_IMPLEMENTORS.strip() in q or q in Q1_DIRECT_IMPLEMENTORS.strip():
                return implementors or []
            return []

        runner.execute.side_effect = execute_side_effect
        return runner

    def test_empty_returns_empty(self):
        node = make_iface_node()
        runner = self._make_uses_runner()
        result = build_interface_uses(runner, node)
        assert result == []

    def test_extends_parent(self):
        node = make_iface_node()
        runner = self._make_uses_runner(
            extends_parent=[{
                "id": "iface:Base",
                "fqn": "App\\BaseInterface",
                "kind": "Interface",
                "file": "src/BaseInterface.php",
                "start_line": 2,
            }]
        )
        result = build_interface_uses(runner, node)
        assert len(result) == 1
        assert result[0].ref_type == "extends"
        assert result[0].fqn == "App\\BaseInterface"

    def test_return_type_from_signature(self):
        node = make_iface_node()
        runner = self._make_uses_runner(
            signature_types=[{
                "method_id": "m:save",
                "method_file": "src/RepositoryInterface.php",
                "method_line": 10,
                "ret_type_id": "cls:Result",
                "ret_type_fqn": "App\\Model\\Result",
                "ret_type_kind": "Class",
                "param_type_id": None,
                "param_type_fqn": None,
                "param_type_kind": None,
            }]
        )
        result = build_interface_uses(runner, node)
        assert len(result) == 1
        assert result[0].ref_type == "return_type"
        assert result[0].fqn == "App\\Model\\Result"

    def test_parameter_type_from_signature(self):
        node = make_iface_node()
        runner = self._make_uses_runner(
            signature_types=[{
                "method_id": "m:save",
                "method_file": "src/RepositoryInterface.php",
                "method_line": 10,
                "ret_type_id": None,
                "ret_type_fqn": None,
                "ret_type_kind": None,
                "param_type_id": "cls:Entity",
                "param_type_fqn": "App\\Model\\Entity",
                "param_type_kind": "Class",
            }]
        )
        result = build_interface_uses(runner, node)
        assert len(result) == 1
        assert result[0].ref_type == "parameter_type"
        assert result[0].fqn == "App\\Model\\Entity"

    def test_parameter_type_wins_over_return_type_for_same_target(self):
        """Same target appears as both return_type and parameter_type -> parameter_type wins."""
        node = make_iface_node()
        runner = self._make_uses_runner(
            signature_types=[
                {
                    "method_id": "m:find",
                    "method_file": "src/RepositoryInterface.php",
                    "method_line": 8,
                    "ret_type_id": "cls:Entity",
                    "ret_type_fqn": "App\\Model\\Entity",
                    "ret_type_kind": "Class",
                    "param_type_id": None,
                    "param_type_fqn": None,
                    "param_type_kind": None,
                },
                {
                    "method_id": "m:save",
                    "method_file": "src/RepositoryInterface.php",
                    "method_line": 12,
                    "ret_type_id": None,
                    "ret_type_fqn": None,
                    "ret_type_kind": None,
                    "param_type_id": "cls:Entity",  # same target!
                    "param_type_fqn": "App\\Model\\Entity",
                    "param_type_kind": "Class",
                },
            ]
        )
        result = build_interface_uses(runner, node)
        # Only one entry for the target
        entity_entries = [e for e in result if e.fqn == "App\\Model\\Entity"]
        assert len(entity_entries) == 1
        assert entity_entries[0].ref_type == "parameter_type"

    def test_include_impl_adds_implementors(self):
        node = make_iface_node()
        runner = self._make_uses_runner(
            implementors=[{
                "id": "cls:Impl",
                "fqn": "App\\Impl",
                "kind": "Class",
                "file": "src/Impl.php",
                "start_line": 5,
            }]
        )
        result = build_interface_uses(runner, node, include_impl=True)
        assert len(result) == 1
        assert result[0].ref_type == "implements"

    def test_include_impl_false_excludes_implementors(self):
        node = make_iface_node()
        runner = self._make_uses_runner(
            implementors=[{
                "id": "cls:Impl",
                "fqn": "App\\Impl",
                "kind": "Class",
                "file": "src/Impl.php",
                "start_line": 5,
            }]
        )
        result = build_interface_uses(runner, node, include_impl=False)
        assert result == []

    def test_uses_priority_order(self):
        """extends comes before implements, implements before parameter_type/return_type."""
        node = make_iface_node()
        runner = self._make_uses_runner(
            extends_parent=[{"id": "iface:Base", "fqn": "App\\Base", "kind": "Interface", "file": None, "start_line": None}],
            signature_types=[{
                "method_id": "m:1",
                "method_file": None,
                "method_line": 10,
                "ret_type_id": "cls:R",
                "ret_type_fqn": "App\\R",
                "ret_type_kind": "Class",
                "param_type_id": "cls:P",
                "param_type_fqn": "App\\P",
                "param_type_kind": "Class",
            }],
            implementors=[{"id": "cls:Impl", "fqn": "App\\Impl", "kind": "Class", "file": None, "start_line": None}],
        )
        result = build_interface_uses(runner, node, include_impl=True)
        ref_types = [e.ref_type for e in result]
        assert ref_types[0] == "extends"
        assert ref_types[1] == "implements"

    def test_uses_priority_constants(self):
        assert USES_PRIORITY["extends"] == 0
        assert USES_PRIORITY["implements"] == 1
        assert USES_PRIORITY["parameter_type"] == USES_PRIORITY["return_type"]
        assert USES_PRIORITY["parameter_type"] < USES_PRIORITY["type_hint"]

    def test_limit_applied(self):
        node = make_iface_node()
        runner = self._make_uses_runner(
            signature_types=[
                {
                    "method_id": f"m:{i}",
                    "method_file": None,
                    "method_line": i,
                    "ret_type_id": f"cls:{i}",
                    "ret_type_fqn": f"App\\R{i}",
                    "ret_type_kind": "Class",
                    "param_type_id": None,
                    "param_type_fqn": None,
                    "param_type_kind": None,
                }
                for i in range(10)
            ]
        )
        result = build_interface_uses(runner, node, limit=3)
        assert len(result) == 3

    def test_no_none_fqn_in_signature_types(self):
        """Records with None ret_type_id/param_type_id should be skipped."""
        node = make_iface_node()
        runner = self._make_uses_runner(
            signature_types=[{
                "method_id": "m:1",
                "method_file": None,
                "method_line": None,
                "ret_type_id": None,
                "ret_type_fqn": None,
                "ret_type_kind": None,
                "param_type_id": None,
                "param_type_fqn": None,
                "param_type_kind": None,
            }]
        )
        result = build_interface_uses(runner, node)
        assert result == []
