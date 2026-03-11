"""Tests for all 7 USED BY handlers.

Tests use mock EdgeContext data -- no Neo4j required.
"""

import pytest

from src.logic.handlers import (
    EdgeContext,
    EntryBucket,
    InstantiationHandler,
    ExtendsHandler,
    ImplementsHandler,
    PropertyTypeHandler,
    MethodCallHandler,
    PropertyAccessHandler,
    ParamReturnHandler,
    USED_BY_HANDLERS,
)


def make_ctx(**overrides) -> EdgeContext:
    """Create an EdgeContext with sensible defaults and overrides."""
    defaults = {
        "start_id": "target:class:Order",
        "source_id": "source:method:create",
        "source_kind": "Method",
        "source_fqn": "App\\Service\\OrderService::createOrder",
        "source_name": "createOrder",
        "source_file": "src/Service/OrderService.php",
        "source_start_line": 42,
        "source_signature": "createOrder(CreateOrderInput $input): Order",
        "target_kind": "Class",
        "target_fqn": "App\\Entity\\Order",
        "target_name": "Order",
        "ref_type": "method_call",
        "file": "src/Service/OrderService.php",
        "line": 55,
        "call_node_id": "call:1",
        "classes_with_injection": frozenset(),
    }
    defaults.update(overrides)
    return EdgeContext(**defaults)


# =============================================================================
# Handler Registry
# =============================================================================


class TestHandlerRegistry:
    """Tests for the USED_BY_HANDLERS registry."""

    def test_all_nine_ref_types_registered(self):
        expected_keys = {
            "instantiation", "extends", "implements", "property_type",
            "method_call", "property_access", "parameter_type", "return_type",
            "type_hint",
        }
        assert set(USED_BY_HANDLERS.keys()) == expected_keys

    def test_param_return_handler_shared(self):
        """parameter_type, return_type, and type_hint share the same handler instance."""
        assert USED_BY_HANDLERS["parameter_type"] is USED_BY_HANDLERS["return_type"]
        assert USED_BY_HANDLERS["parameter_type"] is USED_BY_HANDLERS["type_hint"]

    def test_each_handler_has_handle_method(self):
        for ref_type, handler in USED_BY_HANDLERS.items():
            assert hasattr(handler, "handle"), f"Handler for '{ref_type}' lacks handle()"


# =============================================================================
# InstantiationHandler
# =============================================================================


class TestInstantiationHandler:
    """Tests for InstantiationHandler."""

    def test_basic_instantiation(self):
        handler = InstantiationHandler()
        bucket = EntryBucket()
        ctx = make_ctx(
            ref_type="instantiation",
            containing_method_id="method:create",
            containing_method_fqn="App\\Service\\OrderService::createOrder",
            containing_method_kind="Method",
        )
        handler.handle(ctx, bucket)

        assert len(bucket.instantiation) == 1
        entry = bucket.instantiation[0]
        assert entry["ref_type"] == "instantiation"
        assert entry["node_id"] == "method:create"
        assert entry["fqn"] == "App\\Service\\OrderService::createOrder()"
        assert entry["kind"] == "Method"
        assert entry["depth"] == 1
        assert entry["children"] == []

    def test_dedup_by_containing_method(self):
        """Same containing method should only produce one entry."""
        handler = InstantiationHandler()
        bucket = EntryBucket()
        ctx1 = make_ctx(
            ref_type="instantiation",
            containing_method_id="method:create",
            containing_method_fqn="App\\Svc::create",
            containing_method_kind="Method",
        )
        ctx2 = make_ctx(
            ref_type="instantiation",
            source_id="source:other",
            containing_method_id="method:create",  # Same method
            containing_method_fqn="App\\Svc::create",
            containing_method_kind="Method",
        )
        handler.handle(ctx1, bucket)
        handler.handle(ctx2, bucket)

        assert len(bucket.instantiation) == 1

    def test_different_methods_produce_separate_entries(self):
        handler = InstantiationHandler()
        bucket = EntryBucket()
        ctx1 = make_ctx(
            ref_type="instantiation",
            containing_method_id="method:a",
            containing_method_fqn="App\\Svc::a",
            containing_method_kind="Method",
        )
        ctx2 = make_ctx(
            ref_type="instantiation",
            containing_method_id="method:b",
            containing_method_fqn="App\\Svc::b",
            containing_method_kind="Method",
        )
        handler.handle(ctx1, bucket)
        handler.handle(ctx2, bucket)

        assert len(bucket.instantiation) == 2

    def test_no_containing_method_uses_source(self):
        """When no containing method, fallback to source node."""
        handler = InstantiationHandler()
        bucket = EntryBucket()
        ctx = make_ctx(
            ref_type="instantiation",
            containing_method_id=None,
            containing_method_fqn=None,
            containing_method_kind=None,
        )
        handler.handle(ctx, bucket)

        assert len(bucket.instantiation) == 1
        entry = bucket.instantiation[0]
        assert entry["node_id"] == ctx.source_id
        assert entry["fqn"] == ctx.source_fqn

    def test_arguments_included(self):
        handler = InstantiationHandler()
        bucket = EntryBucket()
        ctx = make_ctx(
            ref_type="instantiation",
            containing_method_id="m:1",
            containing_method_fqn="App\\Svc::m",
            containing_method_kind="Method",
            arguments=({"position": 0, "param_name": "$id", "value_expr": "1"},),
        )
        handler.handle(ctx, bucket)

        entry = bucket.instantiation[0]
        assert len(entry["arguments"]) == 1
        assert entry["arguments"][0]["param_name"] == "$id"


# =============================================================================
# ExtendsHandler
# =============================================================================


class TestExtendsHandler:
    """Tests for ExtendsHandler."""

    def test_basic_extends(self):
        handler = ExtendsHandler()
        bucket = EntryBucket()
        ctx = make_ctx(
            ref_type="extends",
            source_kind="Class",
            source_fqn="App\\Model\\SpecialOrder",
            source_file="src/Model/SpecialOrder.php",
            source_start_line=10,
        )
        handler.handle(ctx, bucket)

        assert len(bucket.extends) == 1
        entry = bucket.extends[0]
        assert entry["ref_type"] == "extends"
        assert entry["fqn"] == "App\\Model\\SpecialOrder"
        assert entry["file"] == "src/Model/SpecialOrder.php"
        assert entry["line"] == 10
        assert entry["kind"] == "Class"

    def test_uses_source_file_and_line(self):
        """ExtendsHandler uses source node's file/line, not edge location."""
        handler = ExtendsHandler()
        bucket = EntryBucket()
        ctx = make_ctx(
            ref_type="extends",
            source_kind="Class",
            source_file="src/Child.php",
            source_start_line=5,
            file="src/Other.php",  # Edge location is different
            line=99,
        )
        handler.handle(ctx, bucket)

        entry = bucket.extends[0]
        assert entry["file"] == "src/Child.php"
        assert entry["line"] == 5


# =============================================================================
# ImplementsHandler
# =============================================================================


class TestImplementsHandler:
    """Tests for ImplementsHandler."""

    def test_basic_implements(self):
        handler = ImplementsHandler()
        bucket = EntryBucket()
        ctx = make_ctx(
            ref_type="implements",
            source_kind="Class",
            source_fqn="App\\Service\\DoctrineOrderRepo",
            source_file="src/Service/DoctrineOrderRepo.php",
            source_start_line=15,
        )
        handler.handle(ctx, bucket)

        assert len(bucket.extends) == 1  # Implements goes into extends bucket
        entry = bucket.extends[0]
        assert entry["ref_type"] == "implements"
        assert entry["fqn"] == "App\\Service\\DoctrineOrderRepo"

    def test_appends_to_extends_bucket(self):
        """Both extends and implements should be in the same bucket."""
        extends_handler = ExtendsHandler()
        implements_handler = ImplementsHandler()
        bucket = EntryBucket()

        extends_ctx = make_ctx(ref_type="extends", source_kind="Class", source_fqn="Child")
        implements_ctx = make_ctx(
            ref_type="implements", source_kind="Class", source_fqn="Implementor"
        )

        extends_handler.handle(extends_ctx, bucket)
        implements_handler.handle(implements_ctx, bucket)

        assert len(bucket.extends) == 2
        assert bucket.extends[0]["ref_type"] == "extends"
        assert bucket.extends[1]["ref_type"] == "implements"


# =============================================================================
# PropertyTypeHandler
# =============================================================================


class TestPropertyTypeHandler:
    """Tests for PropertyTypeHandler."""

    def test_property_source(self):
        """When source is a Property, use its data directly."""
        handler = PropertyTypeHandler()
        bucket = EntryBucket()
        ctx = make_ctx(
            ref_type="property_type",
            source_kind="Property",
            source_id="prop:repo",
            source_fqn="App\\Service\\OrderService::$orderRepository",
            source_file="src/Service/OrderService.php",
            source_start_line=20,
        )
        handler.handle(ctx, bucket)

        assert len(bucket.property_type) == 1
        entry = bucket.property_type[0]
        assert entry["ref_type"] == "property_type"
        assert entry["fqn"] == "App\\Service\\OrderService::$orderRepository"
        assert entry["kind"] == "Property"
        assert entry["node_id"] == "prop:repo"

    def test_dedup_by_property_fqn(self):
        """Same property FQN should only produce one entry."""
        handler = PropertyTypeHandler()
        bucket = EntryBucket()
        ctx1 = make_ctx(
            ref_type="property_type",
            source_kind="Property",
            source_id="prop:1",
            source_fqn="App\\Svc::$repo",
        )
        ctx2 = make_ctx(
            ref_type="property_type",
            source_kind="Property",
            source_id="prop:2",
            source_fqn="App\\Svc::$repo",  # Same FQN
        )
        handler.handle(ctx1, bucket)
        handler.handle(ctx2, bucket)

        assert len(bucket.property_type) == 1

    def test_different_properties_produce_separate_entries(self):
        handler = PropertyTypeHandler()
        bucket = EntryBucket()
        ctx1 = make_ctx(
            ref_type="property_type",
            source_kind="Property",
            source_id="prop:a",
            source_fqn="App\\Svc::$a",
        )
        ctx2 = make_ctx(
            ref_type="property_type",
            source_kind="Property",
            source_id="prop:b",
            source_fqn="App\\Svc::$b",
        )
        handler.handle(ctx1, bucket)
        handler.handle(ctx2, bucket)

        assert len(bucket.property_type) == 2

    def test_method_source_uses_pre_resolved_property(self):
        """When source is Method (constructor promotion), use pre-resolved property data."""
        handler = PropertyTypeHandler()
        bucket = EntryBucket()
        ctx = make_ctx(
            ref_type="property_type",
            source_kind="Method",
            source_id="method:construct",
            property_node_id="prop:repo",
            property_fqn="App\\Svc::$repo",
            property_file="src/Svc.php",
            property_start_line=15,
        )
        handler.handle(ctx, bucket)

        assert len(bucket.property_type) == 1
        entry = bucket.property_type[0]
        assert entry["node_id"] == "prop:repo"
        assert entry["fqn"] == "App\\Svc::$repo"
        assert entry["kind"] == "Property"
        assert entry["file"] == "src/Svc.php"

    def test_method_source_no_property_data_skipped(self):
        """Method source with no property data produces no entry."""
        handler = PropertyTypeHandler()
        bucket = EntryBucket()
        ctx = make_ctx(
            ref_type="property_type",
            source_kind="Method",
            source_id="method:construct",
            # No property_fqn or property_node_id
        )
        handler.handle(ctx, bucket)

        assert len(bucket.property_type) == 0

    def test_non_property_non_method_source_skipped(self):
        """Source kinds other than Property/Method/Function produce no entry."""
        handler = PropertyTypeHandler()
        bucket = EntryBucket()
        ctx = make_ctx(
            ref_type="property_type",
            source_kind="Class",
            source_id="class:1",
        )
        handler.handle(ctx, bucket)

        assert len(bucket.property_type) == 0


# =============================================================================
# MethodCallHandler
# =============================================================================


class TestMethodCallHandler:
    """Tests for MethodCallHandler."""

    def test_basic_method_call(self):
        handler = MethodCallHandler()
        bucket = EntryBucket()
        ctx = make_ctx(
            ref_type="method_call",
            target_kind="Method",
            target_name="save",
            containing_method_id="method:create",
            containing_method_fqn="App\\Svc::create",
            containing_method_kind="Method",
            access_chain="$this->repo",
            on_kind="property",
        )
        handler.handle(ctx, bucket)

        assert len(bucket.method_call) == 1
        entry = bucket.method_call[0]
        assert entry["ref_type"] == "method_call"
        assert entry["callee"] == "save()"
        assert entry["on"] == "$this->repo"
        assert entry["on_kind"] == "property"
        assert entry["fqn"] == "App\\Svc::create()"

    def test_suppressed_when_class_has_injection(self):
        """Method call from a class with injection should be suppressed."""
        handler = MethodCallHandler()
        bucket = EntryBucket()
        ctx = make_ctx(
            ref_type="method_call",
            containing_class_id="class:svc",
            classes_with_injection=frozenset({"class:svc"}),
            containing_method_id="method:1",
            containing_method_fqn="App\\Svc::m",
            containing_method_kind="Method",
        )
        handler.handle(ctx, bucket)

        assert len(bucket.method_call) == 0

    def test_not_suppressed_different_class(self):
        """Method call from a class WITHOUT injection is not suppressed."""
        handler = MethodCallHandler()
        bucket = EntryBucket()
        ctx = make_ctx(
            ref_type="method_call",
            containing_class_id="class:other",
            classes_with_injection=frozenset({"class:svc"}),
            containing_method_id="method:1",
            containing_method_fqn="App\\Other::m",
            containing_method_kind="Method",
        )
        handler.handle(ctx, bucket)

        assert len(bucket.method_call) == 1

    def test_no_containing_class_not_suppressed(self):
        """No containing class -> never suppressed."""
        handler = MethodCallHandler()
        bucket = EntryBucket()
        ctx = make_ctx(
            ref_type="method_call",
            containing_class_id=None,
            classes_with_injection=frozenset({"class:svc"}),
        )
        handler.handle(ctx, bucket)

        assert len(bucket.method_call) == 1

    def test_callee_name_only_for_method_target(self):
        """callee is only set when target is a Method."""
        handler = MethodCallHandler()
        bucket = EntryBucket()
        ctx = make_ctx(
            ref_type="method_call",
            target_kind="Function",
            target_name="array_map",
        )
        handler.handle(ctx, bucket)

        entry = bucket.method_call[0]
        assert entry["callee"] is None

    def test_no_containing_method_uses_source(self):
        handler = MethodCallHandler()
        bucket = EntryBucket()
        ctx = make_ctx(
            ref_type="method_call",
            target_kind="Method",
            target_name="save",
            containing_method_id=None,
            containing_method_fqn=None,
            containing_method_kind=None,
        )
        handler.handle(ctx, bucket)

        entry = bucket.method_call[0]
        assert entry["node_id"] == ctx.source_id
        assert entry["fqn"] == ctx.source_fqn

    def test_arguments_included(self):
        handler = MethodCallHandler()
        bucket = EntryBucket()
        ctx = make_ctx(
            ref_type="method_call",
            target_kind="Method",
            target_name="save",
            arguments=({"position": 0, "param_name": "$order"},),
        )
        handler.handle(ctx, bucket)

        assert len(bucket.method_call[0]["arguments"]) == 1


# =============================================================================
# PropertyAccessHandler
# =============================================================================


class TestPropertyAccessHandler:
    """Tests for PropertyAccessHandler."""

    def test_basic_property_access(self):
        handler = PropertyAccessHandler()
        bucket = EntryBucket()
        ctx = make_ctx(
            ref_type="property_access",
            target_fqn="App\\Entity\\Order::$status",
            target_name="status",
            containing_method_id="method:check",
            containing_method_fqn="App\\Svc::check",
            containing_method_kind="Method",
            access_chain="$this->order",
            on_kind="property",
        )
        handler.handle(ctx, bucket)

        assert "App\\Entity\\Order::$status" in bucket.property_access_groups
        groups = bucket.property_access_groups["App\\Entity\\Order::$status"]
        assert len(groups) == 1
        assert groups[0]["method_fqn"] == "App\\Svc::check"
        assert groups[0]["on_expr"] == "$this->order"
        assert groups[0]["lines"] == [55]  # Default line from make_ctx

    def test_same_method_same_on_groups_lines(self):
        """Multiple accesses from same method/on_expr group together."""
        handler = PropertyAccessHandler()
        bucket = EntryBucket()
        base = {
            "ref_type": "property_access",
            "target_fqn": "App\\Order::$status",
            "target_name": "status",
            "containing_method_id": "method:m",
            "containing_method_fqn": "App\\Svc::m",
            "containing_method_kind": "Method",
            "access_chain": "$this->order",
            "on_kind": "property",
        }
        handler.handle(make_ctx(**base, line=10), bucket)
        handler.handle(make_ctx(**base, line=20), bucket)

        groups = bucket.property_access_groups["App\\Order::$status"]
        assert len(groups) == 1
        assert groups[0]["lines"] == [10, 20]

    def test_different_on_expr_creates_separate_groups(self):
        handler = PropertyAccessHandler()
        bucket = EntryBucket()
        base = {
            "ref_type": "property_access",
            "target_fqn": "App\\Order::$status",
            "target_name": "status",
            "containing_method_id": "method:m",
            "containing_method_fqn": "App\\Svc::m",
            "containing_method_kind": "Method",
        }
        handler.handle(make_ctx(**base, access_chain="$this->order", on_kind="property"), bucket)
        handler.handle(make_ctx(**base, access_chain="$param", on_kind="param"), bucket)

        groups = bucket.property_access_groups["App\\Order::$status"]
        assert len(groups) == 2

    def test_different_methods_create_separate_groups(self):
        handler = PropertyAccessHandler()
        bucket = EntryBucket()

        handler.handle(make_ctx(
            ref_type="property_access",
            target_fqn="App\\Order::$id",
            containing_method_id="method:a",
            containing_method_fqn="App\\Svc::a",
            containing_method_kind="Method",
        ), bucket)
        handler.handle(make_ctx(
            ref_type="property_access",
            target_fqn="App\\Order::$id",
            containing_method_id="method:b",
            containing_method_fqn="App\\Svc::b",
            containing_method_kind="Method",
        ), bucket)

        groups = bucket.property_access_groups["App\\Order::$id"]
        assert len(groups) == 2


# =============================================================================
# ParamReturnHandler
# =============================================================================


class TestParamReturnHandler:
    """Tests for ParamReturnHandler."""

    def test_return_type_shows_method_fqn(self):
        handler = ParamReturnHandler()
        bucket = EntryBucket()
        ctx = make_ctx(
            ref_type="return_type",
            source_kind="Method",
            source_fqn="App\\Svc::getOrder",
            source_file="src/Svc.php",
            source_start_line=30,
            source_signature="getOrder(): Order",
        )
        handler.handle(ctx, bucket)

        assert len(bucket.param_return) == 1
        entry = bucket.param_return[0]
        assert entry["fqn"] == "App\\Svc::getOrder()"
        assert entry["ref_type"] == "return_type"
        assert entry["signature"] == "getOrder(): Order"

    def test_return_type_dedup_by_method_fqn(self):
        handler = ParamReturnHandler()
        bucket = EntryBucket()
        base = {
            "ref_type": "return_type",
            "source_kind": "Method",
            "source_fqn": "App\\Svc::getOrder",
        }
        handler.handle(make_ctx(**base), bucket)
        handler.handle(make_ctx(**base), bucket)

        assert len(bucket.param_return) == 1

    def test_return_type_function(self):
        handler = ParamReturnHandler()
        bucket = EntryBucket()
        ctx = make_ctx(
            ref_type="return_type",
            source_kind="Function",
            source_fqn="getUser",
        )
        handler.handle(ctx, bucket)

        assert len(bucket.param_return) == 1
        # Functions don't get () appended by format_method_fqn
        assert bucket.param_return[0]["fqn"] == "getUser"

    def test_parameter_type_groups_by_class(self):
        handler = ParamReturnHandler()
        bucket = EntryBucket()
        ctx = make_ctx(
            ref_type="parameter_type",
            source_kind="Method",
            source_fqn="App\\Svc::create",
            containing_class_id="class:svc",
        )
        handler.handle(ctx, bucket)

        # Since source_kind is Method (not Class), and we have containing_class_id,
        # it should create an entry with the containing class data
        assert len(bucket.param_return) == 1

    def test_parameter_type_class_source(self):
        """When source IS the class, use it directly."""
        handler = ParamReturnHandler()
        bucket = EntryBucket()
        ctx = make_ctx(
            ref_type="parameter_type",
            source_kind="Class",
            source_id="class:svc",
            source_fqn="App\\Service\\OrderService",
            source_file="src/Service/OrderService.php",
            source_start_line=5,
        )
        handler.handle(ctx, bucket)

        assert len(bucket.param_return) == 1
        entry = bucket.param_return[0]
        assert entry["fqn"] == "App\\Service\\OrderService"
        assert entry["node_id"] == "class:svc"

    def test_parameter_type_dedup_by_class_fqn(self):
        handler = ParamReturnHandler()
        bucket = EntryBucket()
        base = {
            "ref_type": "parameter_type",
            "source_kind": "Class",
            "source_id": "class:svc",
            "source_fqn": "App\\Svc",
        }
        handler.handle(make_ctx(**base), bucket)
        handler.handle(make_ctx(**base), bucket)

        assert len(bucket.param_return) == 1

    def test_type_hint_groups_by_class(self):
        handler = ParamReturnHandler()
        bucket = EntryBucket()
        ctx = make_ctx(
            ref_type="type_hint",
            source_kind="Class",
            source_id="class:svc",
            source_fqn="App\\Service\\OrderService",
            containing_class_id="class:svc",
        )
        handler.handle(ctx, bucket)

        assert len(bucket.param_return) == 1
        assert bucket.param_return[0]["ref_type"] == "type_hint"

    def test_no_containing_class_skipped(self):
        """When no class can be found, entry is skipped."""
        handler = ParamReturnHandler()
        bucket = EntryBucket()
        ctx = make_ctx(
            ref_type="parameter_type",
            source_kind="Method",
            source_fqn="App\\Svc::m",
            containing_class_id=None,
        )
        handler.handle(ctx, bucket)

        assert len(bucket.param_return) == 0

    def test_return_type_method_fqn_already_has_parens(self):
        """Method FQN already ending with () shouldn't get double parens."""
        handler = ParamReturnHandler()
        bucket = EntryBucket()
        ctx = make_ctx(
            ref_type="return_type",
            source_kind="Method",
            source_fqn="App\\Svc::getOrder()",
        )
        handler.handle(ctx, bucket)

        assert bucket.param_return[0]["fqn"] == "App\\Svc::getOrder()"


# =============================================================================
# EntryBucket
# =============================================================================


class TestEntryBucket:
    """Tests for EntryBucket structure."""

    def test_empty_bucket(self):
        bucket = EntryBucket()
        assert bucket.instantiation == []
        assert bucket.extends == []
        assert bucket.property_type == []
        assert bucket.method_call == []
        assert bucket.property_access_groups == {}
        assert bucket.param_return == []
        assert bucket.seen_instantiation_methods == set()
        assert bucket.seen_property_type_props == set()

    def test_bucket_mutability(self):
        bucket = EntryBucket()
        bucket.instantiation.append({"test": True})
        bucket.seen_instantiation_methods.add("m:1")
        assert len(bucket.instantiation) == 1
        assert "m:1" in bucket.seen_instantiation_methods


# =============================================================================
# EdgeContext
# =============================================================================


class TestEdgeContext:
    """Tests for EdgeContext dataclass."""

    def test_frozen(self):
        ctx = make_ctx()
        with pytest.raises(AttributeError):
            ctx.ref_type = "other"  # type: ignore

    def test_default_optional_fields(self):
        ctx = EdgeContext(
            start_id="t",
            source_id="s",
            source_kind="Method",
            source_fqn="fqn",
            source_name="name",
            source_file=None,
            source_start_line=None,
            source_signature=None,
            target_kind="Class",
            target_fqn="tfqn",
            target_name="tname",
            ref_type="uses",
            file=None,
            line=None,
            call_node_id=None,
            classes_with_injection=frozenset(),
        )
        assert ctx.containing_method_id is None
        assert ctx.arguments == ()
        assert ctx.property_node_id is None
