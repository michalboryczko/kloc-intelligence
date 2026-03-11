"""Tests for definition builders with mock data (no Neo4j)."""

from src.logic.definition import (
    build_definition,
    build_method_definition,
    build_class_definition,
    build_interface_definition,
    build_property_definition,
    build_argument_definition,
    build_value_definition,
    parse_property_doc,
)
from src.models.results import DefinitionInfo


def _make_node(
    fqn="App\\TestClass",
    kind="Class",
    file="src/TestClass.php",
    start_line=10,
    signature=None,
    documentation=None,
    value_kind=None,
    name="TestClass",
    node_id="test-node",
):
    """Create a mock node dict."""
    return {
        "node_id": node_id,
        "kind": kind,
        "name": name,
        "fqn": fqn,
        "file": file,
        "start_line": start_line,
        "signature": signature,
        "documentation": documentation or [],
        "value_kind": value_kind,
    }


class TestBuildDefinitionDispatch:
    """Test build_definition dispatches to correct sub-builder."""

    def test_returns_unknown_when_no_node(self):
        result = build_definition({"node": None})
        assert result.fqn == "unknown"
        assert result.kind == "unknown"

    def test_returns_unknown_when_empty_data(self):
        result = build_definition({})
        assert result.fqn == "unknown"
        assert result.kind == "unknown"

    def test_basic_node_fields(self):
        data = {"node": _make_node()}
        result = build_definition(data)
        assert result.fqn == "App\\TestClass"
        assert result.kind == "Class"
        assert result.file == "src/TestClass.php"
        assert result.line == 10

    def test_parent_sets_declared_in(self):
        data = {
            "node": _make_node(kind="Method", fqn="App\\Foo::bar()"),
            "parent": {
                "fqn": "App\\Foo",
                "kind": "Class",
                "file": "src/Foo.php",
                "start_line": 5,
            },
        }
        result = build_definition(data)
        assert result.declared_in is not None
        assert result.declared_in["fqn"] == "App\\Foo"
        assert result.declared_in["kind"] == "Class"
        assert result.declared_in["line"] == 5

    def test_dispatches_to_method(self):
        data = {"node": _make_node(kind="Method", fqn="App\\Foo::bar()")}
        result = build_definition(data)
        assert result.kind == "Method"

    def test_dispatches_to_function(self):
        data = {"node": _make_node(kind="Function", fqn="App\\helper()")}
        result = build_definition(data)
        assert result.kind == "Function"

    def test_dispatches_to_class(self):
        data = {"node": _make_node(kind="Class")}
        result = build_definition(data)
        assert result.kind == "Class"

    def test_dispatches_to_interface(self):
        data = {"node": _make_node(kind="Interface", fqn="App\\FooInterface")}
        result = build_definition(data)
        assert result.kind == "Interface"

    def test_dispatches_to_property(self):
        data = {"node": _make_node(kind="Property", fqn="App\\Foo::$bar", name="$bar")}
        result = build_definition(data)
        assert result.kind == "Property"

    def test_dispatches_to_argument(self):
        data = {"node": _make_node(kind="Argument", fqn="App\\Foo::bar().$x", name="$x")}
        result = build_definition(data)
        assert result.kind == "Argument"

    def test_dispatches_to_value(self):
        data = {"node": _make_node(kind="Value", fqn="val.0", value_kind="local")}
        result = build_definition(data)
        assert result.kind == "Value"


class TestBuildMethodDefinition:
    """Test method/function definition building."""

    def test_collects_typed_arguments(self):
        data = {
            "node": _make_node(kind="Method", fqn="App\\Foo::bar()"),
            "children": [
                {"node_id": "arg1", "kind": "Argument", "name": "$x"},
                {"node_id": "arg2", "kind": "Argument", "name": "$y"},
            ],
            "child_type_hints": {
                "arg1": [{"fqn": "string", "name": "string"}],
            },
            "type_hints": [],
        }
        info = DefinitionInfo(fqn="App\\Foo::bar()", kind="Method")
        build_method_definition(data, info)
        assert len(info.arguments) == 2
        assert info.arguments[0]["name"] == "$x"
        assert info.arguments[0]["type"] == "string"
        assert info.arguments[1]["name"] == "$y"
        assert "type" not in info.arguments[1]

    def test_resolves_return_type(self):
        data = {
            "node": _make_node(kind="Method", fqn="App\\Foo::bar()"),
            "children": [],
            "child_type_hints": {},
            "type_hints": [{"fqn": "App\\Entity\\User", "name": "User"}],
        }
        info = DefinitionInfo(fqn="App\\Foo::bar()", kind="Method")
        build_method_definition(data, info)
        assert info.return_type == {"fqn": "App\\Entity\\User", "name": "User"}

    def test_no_return_type_when_empty(self):
        data = {
            "node": _make_node(kind="Method"),
            "children": [],
            "child_type_hints": {},
            "type_hints": [],
        }
        info = DefinitionInfo(fqn="test", kind="Method")
        build_method_definition(data, info)
        assert info.return_type is None

    def test_skips_non_argument_children(self):
        data = {
            "node": _make_node(kind="Method"),
            "children": [
                {"node_id": "val1", "kind": "Value", "name": "$tmp"},
                {"node_id": "arg1", "kind": "Argument", "name": "$x"},
            ],
            "child_type_hints": {},
            "type_hints": [],
        }
        info = DefinitionInfo(fqn="test", kind="Method")
        build_method_definition(data, info)
        assert len(info.arguments) == 1
        assert info.arguments[0]["name"] == "$x"


class TestBuildClassDefinition:
    """Test class definition building."""

    def test_collects_properties(self):
        data = {
            "node": _make_node(kind="Class"),
            "children": [
                {
                    "node_id": "p1",
                    "kind": "Property",
                    "name": "$email",
                    "documentation": ['```php\npublic string $email\n```'],
                },
            ],
            "child_type_hints": {},
            "overrides": {},
            "promoted_properties": set(),
            "inheritance": {},
        }
        info = DefinitionInfo(fqn="App\\User", kind="Class")
        build_class_definition(data, info)
        assert len(info.properties) == 1
        assert info.properties[0]["name"] == "$email"
        assert info.properties[0]["visibility"] == "public"
        assert info.properties[0]["type"] == "string"

    def test_collects_methods_with_override_tag(self):
        data = {
            "node": _make_node(kind="Class"),
            "children": [
                {
                    "node_id": "m1",
                    "kind": "Method",
                    "name": "getId",
                    "signature": "getId(): int",
                    "documentation": [],
                },
                {
                    "node_id": "m2",
                    "kind": "Method",
                    "name": "getName",
                    "signature": "getName(): string",
                    "documentation": [],
                },
            ],
            "child_type_hints": {},
            "overrides": {"m1": "parent-m1"},
            "promoted_properties": set(),
            "inheritance": {},
        }
        info = DefinitionInfo(fqn="App\\User", kind="Class")
        build_class_definition(data, info)
        assert len(info.methods) == 2
        # Override method should be sorted first
        assert info.methods[0]["name"] == "getId"
        assert info.methods[0]["tags"] == ["override"]
        assert info.methods[1]["name"] == "getName"
        assert "tags" not in info.methods[1]

    def test_skips_constructor(self):
        data = {
            "node": _make_node(kind="Class"),
            "children": [
                {"node_id": "ctor", "kind": "Method", "name": "__construct", "documentation": []},
                {"node_id": "m1", "kind": "Method", "name": "doStuff", "documentation": []},
            ],
            "child_type_hints": {},
            "overrides": {},
            "promoted_properties": set(),
            "inheritance": {},
        }
        info = DefinitionInfo(fqn="App\\Foo", kind="Class")
        build_class_definition(data, info)
        assert len(info.methods) == 1
        assert info.methods[0]["name"] == "doStuff"

    def test_abstract_method_tag(self):
        data = {
            "node": _make_node(kind="Class"),
            "children": [
                {
                    "node_id": "m1",
                    "kind": "Method",
                    "name": "process",
                    "documentation": ["```php\nabstract public function process(): void\n```"],
                },
            ],
            "child_type_hints": {},
            "overrides": {},
            "promoted_properties": set(),
            "inheritance": {},
        }
        info = DefinitionInfo(fqn="App\\Base", kind="Class")
        build_class_definition(data, info)
        assert info.methods[0]["tags"] == ["abstract"]

    def test_constructor_deps_from_promoted(self):
        data = {
            "node": _make_node(kind="Class"),
            "children": [
                {
                    "node_id": "p1",
                    "kind": "Property",
                    "name": "$service",
                    "documentation": ['```php\nprivate readonly Service $service\n```'],
                },
            ],
            "child_type_hints": {
                "p1": [{"fqn": "App\\Service", "name": "Service"}],
            },
            "overrides": {},
            "promoted_properties": {"p1"},
            "inheritance": {},
        }
        info = DefinitionInfo(fqn="App\\Foo", kind="Class")
        build_class_definition(data, info)
        assert len(info.constructor_deps) == 1
        assert info.constructor_deps[0]["name"] == "$service"
        assert info.constructor_deps[0]["type"] == "Service"

    def test_constructor_deps_type_from_docs(self):
        """When no type_hint edge, falls back to doc type."""
        data = {
            "node": _make_node(kind="Class"),
            "children": [
                {
                    "node_id": "p1",
                    "kind": "Property",
                    "name": "$count",
                    "documentation": ['```php\nprivate int $count\n```'],
                },
            ],
            "child_type_hints": {},
            "overrides": {},
            "promoted_properties": {"p1"},
            "inheritance": {},
        }
        info = DefinitionInfo(fqn="App\\Foo", kind="Class")
        build_class_definition(data, info)
        assert len(info.constructor_deps) == 1
        assert info.constructor_deps[0]["type"] == "int"

    def test_inheritance_extends(self):
        data = {
            "node": _make_node(kind="Class"),
            "children": [],
            "child_type_hints": {},
            "overrides": {},
            "promoted_properties": set(),
            "inheritance": {
                "extends_fqn": "App\\BaseClass",
                "implements_fqns": ["App\\FooInterface"],
                "uses_trait_fqns": ["App\\LoggableTrait"],
            },
        }
        info = DefinitionInfo(fqn="App\\Derived", kind="Class")
        build_class_definition(data, info)
        assert info.extends == "App\\BaseClass"
        assert info.implements == ["App\\FooInterface"]
        assert info.uses_traits == ["App\\LoggableTrait"]

    def test_delegates_to_interface_builder(self):
        data = {
            "node": _make_node(kind="Interface", fqn="App\\FooInterface"),
            "children": [
                {"node_id": "m1", "kind": "Method", "name": "process", "signature": "process(): void"},
            ],
            "child_type_hints": {},
            "overrides": {},
            "promoted_properties": set(),
            "inheritance": {"extends_fqn": "App\\BaseInterface", "implements_fqns": [], "uses_trait_fqns": []},
        }
        info = DefinitionInfo(fqn="App\\FooInterface", kind="Interface")
        build_class_definition(data, info)
        assert len(info.methods) == 1
        assert info.methods[0]["name"] == "process"
        assert info.extends == "App\\BaseInterface"
        # Interfaces should not have properties
        assert info.properties == []


class TestBuildInterfaceDefinition:
    """Test interface definition building."""

    def test_collects_methods(self):
        data = {
            "children": [
                {"kind": "Method", "name": "execute", "signature": "execute(): bool"},
                {"kind": "Method", "name": "validate", "signature": "validate(): void"},
            ],
            "inheritance": {},
        }
        info = DefinitionInfo(fqn="App\\IHandler", kind="Interface")
        build_interface_definition(data, info)
        assert len(info.methods) == 2
        assert info.methods[0]["name"] == "execute"
        assert info.methods[0]["signature"] == "execute(): bool"

    def test_extends_interface(self):
        data = {
            "children": [],
            "inheritance": {"extends_fqn": "App\\IBase"},
        }
        info = DefinitionInfo(fqn="App\\IChild", kind="Interface")
        build_interface_definition(data, info)
        assert info.extends == "App\\IBase"


class TestBuildPropertyDefinition:
    """Test property definition building."""

    def test_type_from_type_hint(self):
        data = {
            "node": _make_node(kind="Property", name="$user", fqn="App\\Foo::$user", node_id="p1"),
            "type_hints": [{"fqn": "App\\Entity\\User", "name": "User"}],
            "promoted_properties": set(),
            "parent": None,
        }
        info = DefinitionInfo(fqn="App\\Foo::$user", kind="Property")
        build_property_definition(data, info)
        assert info.return_type["fqn"] == "App\\Entity\\User"
        assert info.return_type["name"] == "User"

    def test_visibility_from_docs(self):
        data = {
            "node": _make_node(
                kind="Property",
                name="$email",
                fqn="App\\Foo::$email",
                node_id="p1",
                documentation=['```php\nprivate string $email\n```'],
            ),
            "type_hints": [],
            "promoted_properties": set(),
            "parent": None,
        }
        info = DefinitionInfo(fqn="App\\Foo::$email", kind="Property")
        build_property_definition(data, info)
        assert info.return_type["visibility"] == "private"
        assert info.return_type["name"] == "string"

    def test_readonly_and_static(self):
        data = {
            "node": _make_node(
                kind="Property",
                name="$instance",
                fqn="App\\Foo::$instance",
                node_id="p1",
                documentation=['```php\nprivate static readonly Foo $instance\n```'],
            ),
            "type_hints": [],
            "promoted_properties": set(),
            "parent": None,
        }
        info = DefinitionInfo(fqn="App\\Foo::$instance", kind="Property")
        build_property_definition(data, info)
        assert info.return_type["readonly"] is True
        assert info.return_type["static"] is True

    def test_promoted_property(self):
        data = {
            "node": _make_node(kind="Property", name="$service", fqn="App\\Foo::$service", node_id="p1"),
            "type_hints": [{"fqn": "App\\Service", "name": "Service"}],
            "promoted_properties": {"p1"},
            "parent": None,
        }
        info = DefinitionInfo(fqn="App\\Foo::$service", kind="Property")
        build_property_definition(data, info)
        assert info.return_type["promoted"] is True

    def test_readonly_class_makes_property_readonly(self):
        data = {
            "node": _make_node(kind="Property", name="$name", fqn="App\\Foo::$name", node_id="p1"),
            "type_hints": [],
            "promoted_properties": set(),
            "parent": {
                "kind": "Class",
                "documentation": ["```php\nreadonly class Foo\n```"],
            },
        }
        info = DefinitionInfo(fqn="App\\Foo::$name", kind="Property")
        build_property_definition(data, info)
        assert info.return_type is not None
        assert info.return_type.get("readonly") is True


class TestBuildArgumentDefinition:
    """Test argument definition building."""

    def test_type_from_type_hint(self):
        data = {
            "node": _make_node(kind="Argument", name="$id"),
            "type_hints": [{"fqn": "int", "name": "int"}],
        }
        info = DefinitionInfo(fqn="App\\Foo::bar().$id", kind="Argument")
        build_argument_definition(data, info)
        assert info.return_type == {"fqn": "int", "name": "int"}

    def test_no_type_when_empty(self):
        data = {
            "node": _make_node(kind="Argument", name="$x"),
            "type_hints": [],
        }
        info = DefinitionInfo(fqn="test.$x", kind="Argument")
        build_argument_definition(data, info)
        assert info.return_type is None


class TestBuildValueDefinition:
    """Test value definition building."""

    def test_value_kind_set(self):
        data = {
            "node": _make_node(kind="Value", value_kind="local"),
        }
        info = DefinitionInfo(fqn="val.0", kind="Value")
        build_value_definition(data, info)
        assert info.value_kind == "local"

    def test_type_of_single(self):
        data = {
            "node": _make_node(kind="Value", value_kind="local"),
            "type_of": [{"fqn": "App\\User", "name": "User"}],
        }
        info = DefinitionInfo(fqn="val.0", kind="Value")
        build_value_definition(data, info)
        assert info.type_info == {"fqn": "App\\User", "name": "User"}

    def test_type_of_union(self):
        data = {
            "node": _make_node(kind="Value", value_kind="local"),
            "type_of": [
                {"fqn": "App\\User", "name": "User"},
                {"fqn": "App\\Admin", "name": "Admin"},
            ],
        }
        info = DefinitionInfo(fqn="val.0", kind="Value")
        build_value_definition(data, info)
        assert info.type_info["name"] == "User|Admin"
        assert info.type_info["fqn"] == "App\\User|App\\Admin"

    def test_source_from_property(self):
        data = {
            "node": _make_node(kind="Value", value_kind="local"),
            "value_source": {
                "af": {"kind": "Property", "fqn": "App\\Foo::$bar", "file": "f.php", "start_line": 5},
                "call": None,
                "callee": None,
            },
        }
        info = DefinitionInfo(fqn="val.0", kind="Value")
        build_value_definition(data, info)
        assert info.source["method_name"] == "promotes to App\\Foo::$bar"

    def test_source_from_call(self):
        data = {
            "node": _make_node(kind="Value", value_kind="local"),
            "value_source": {
                "af": {"kind": "Value", "fqn": "af.0"},
                "call": {"fqn": "call.0", "file": "f.php", "start_line": 10},
                "callee": {"kind": "Method", "name": "findById", "fqn": "App\\Repo::findById()"},
            },
        }
        info = DefinitionInfo(fqn="val.0", kind="Value")
        build_value_definition(data, info)
        assert info.source["method_fqn"] == "App\\Repo::findById()"
        assert info.source["method_name"] == "findById()"

    def test_result_value_source(self):
        data = {
            "node": _make_node(kind="Value", value_kind="result"),
            "value_source": {},
            "result_source": {
                "call": {"fqn": "call.0", "file": "f.php", "start_line": 20},
                "callee": {"kind": "Function", "name": "array_map", "fqn": "array_map"},
            },
        }
        info = DefinitionInfo(fqn="val.0", kind="Value")
        build_value_definition(data, info)
        assert info.source["method_name"] == "array_map()"
        assert info.source["call_fqn"] == "call.0"

    def test_scope_sets_declared_in(self):
        data = {
            "node": _make_node(kind="Value", value_kind="local"),
            "scope": {"fqn": "App\\Foo::bar()", "kind": "Method", "file": "f.php", "start_line": 3},
        }
        info = DefinitionInfo(fqn="val.0", kind="Value")
        # No pre-existing declared_in
        build_value_definition(data, info)
        assert info.declared_in is not None
        assert info.declared_in["fqn"] == "App\\Foo::bar()"

    def test_scope_does_not_override_declared_in(self):
        data = {
            "node": _make_node(kind="Value", value_kind="local"),
            "scope": {"fqn": "App\\Foo::bar()", "kind": "Method", "file": "f.php", "start_line": 3},
        }
        info = DefinitionInfo(fqn="val.0", kind="Value")
        info.declared_in = {"fqn": "App\\Foo", "kind": "Class"}
        build_value_definition(data, info)
        assert info.declared_in["fqn"] == "App\\Foo"


class TestParsePropertyDoc:
    """Test parse_property_doc function."""

    def test_public_string(self):
        vis, ro, static, dtype = parse_property_doc(
            ['```php\npublic string $email\n```'], "$email"
        )
        assert vis == "public"
        assert dtype == "string"
        assert ro is False
        assert static is False

    def test_private_readonly(self):
        vis, ro, static, dtype = parse_property_doc(
            ['```php\nprivate readonly \\App\\Service $service\n```'], "$service"
        )
        assert vis == "private"
        assert ro is True
        assert dtype == "Service"

    def test_private_static(self):
        vis, ro, static, dtype = parse_property_doc(
            ['```php\nprivate static array $items = []\n```'], "$items"
        )
        assert vis == "private"
        assert static is True
        assert dtype == "array"

    def test_protected(self):
        vis, ro, static, dtype = parse_property_doc(
            ['```php\nprotected int $count\n```'], "$count"
        )
        assert vis == "protected"
        assert dtype == "int"

    def test_no_documentation(self):
        vis, ro, static, dtype = parse_property_doc(None, "$x")
        assert vis is None
        assert dtype is None

    def test_empty_documentation(self):
        vis, ro, static, dtype = parse_property_doc([], "$x")
        assert vis is None
        assert dtype is None

    def test_namespace_prefix_stripped(self):
        vis, ro, static, dtype = parse_property_doc(
            ['```php\npublic \\App\\Entity\\User $user\n```'], "$user"
        )
        assert dtype == "User"

    def test_no_matching_name(self):
        """If the property name is not in the doc line, skip it."""
        vis, ro, static, dtype = parse_property_doc(
            ['```php\npublic string $other\n```'], "$missing"
        )
        assert vis is None
        assert dtype is None
