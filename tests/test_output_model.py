"""Tests for ContextOutput model serialization.

Tests cover:
- Line number conversion (0-based -> 1-based)
- camelCase field names in output
- class_level vs method_level mode differences
- Field suppression rules
- OutputDefinition property-specific type extraction
"""

from src.models.node import NodeData
from src.models.results import (
    ArgumentInfo,
    ContextEntry,
    ContextResult,
    DefinitionInfo,
    MemberRef,
)
from src.models.output import (
    ContextOutput,
    OutputArgumentInfo,
    OutputDefinition,
    OutputEntry,
    OutputMemberRef,
    OutputTarget,
    _shorten_param_key,
)


def _make_node(**overrides) -> NodeData:
    """Create a test NodeData with defaults."""
    defaults = {
        "node_id": "target-1",
        "kind": "Method",
        "name": "findById",
        "fqn": "App\\Repo::findById()",
        "symbol": "scip-php . . App/Repo#findById().",
        "file": "src/Repo.php",
        "start_line": 20,
    }
    defaults.update(overrides)
    return NodeData(**defaults)


def _make_context_entry(**overrides) -> ContextEntry:
    """Create a test ContextEntry with defaults."""
    defaults = {
        "depth": 1,
        "node_id": "source-1",
        "fqn": "App\\Service::process()",
        "kind": "Method",
        "file": "src/Service.php",
        "line": 42,
    }
    defaults.update(overrides)
    return ContextEntry(**defaults)


class TestShortenParamKey:
    """Test _shorten_param_key helper."""

    def test_shortens_namespaced_fqn(self):
        result = _shorten_param_key("App\\Service\\Foo::$bar", None, 0)
        assert result == "Foo::$bar"

    def test_uses_param_name_when_no_fqn(self):
        result = _shorten_param_key(None, "$x", 0)
        assert result == "$x"

    def test_uses_position_fallback(self):
        result = _shorten_param_key(None, None, 2)
        assert result == "arg[2]"

    def test_no_namespace_in_fqn(self):
        result = _shorten_param_key("Foo::$bar", None, 0)
        assert result == "Foo::$bar"

    def test_fqn_without_double_colon(self):
        result = _shorten_param_key("just_a_name", None, 0)
        assert result == "just_a_name"


class TestOutputArgumentInfo:
    """Test OutputArgumentInfo conversion and serialization."""

    def test_from_info(self):
        info = ArgumentInfo(
            position=0,
            param_name="$id",
            value_expr="42",
            value_source="literal",
        )
        out = OutputArgumentInfo.from_info(info)
        assert out.position == 0
        assert out.param_name == "$id"
        assert out.value_expr == "42"
        assert out.value_source == "literal"

    def test_to_dict_required_fields(self):
        out = OutputArgumentInfo(position=0, param_name="$id", value_expr="42", value_source="literal")
        d = out.to_dict()
        assert d == {
            "position": 0,
            "param_name": "$id",
            "value_expr": "42",
            "value_source": "literal",
        }

    def test_to_dict_optional_fields(self):
        out = OutputArgumentInfo(
            position=0,
            param_name="$id",
            value_expr="42",
            value_source="literal",
            value_type="int",
            param_fqn="App\\Foo::$id",
            value_ref_symbol="sym:1",
            source_chain=["a", "b"],
        )
        d = out.to_dict()
        assert d["value_type"] == "int"
        assert d["param_fqn"] == "App\\Foo::$id"
        assert d["value_ref_symbol"] == "sym:1"
        assert d["source_chain"] == ["a", "b"]

    def test_to_dict_omits_none_optional_fields(self):
        out = OutputArgumentInfo(position=0, param_name=None, value_expr=None, value_source=None)
        d = out.to_dict()
        assert "value_type" not in d
        assert "param_fqn" not in d
        assert "value_ref_symbol" not in d
        assert "source_chain" not in d


class TestOutputMemberRef:
    """Test OutputMemberRef conversion and serialization."""

    def test_from_ref_line_conversion(self):
        ref = MemberRef(
            target_name="getId",
            target_fqn="App\\User::getId()",
            target_kind="Method",
            file="src/User.php",
            line=10,  # 0-based
            on_line=5,  # 0-based
        )
        out = OutputMemberRef.from_ref(ref)
        assert out.line == 11  # 1-based
        assert out.on_line == 6  # 1-based

    def test_from_ref_none_lines(self):
        ref = MemberRef(target_name="x", target_fqn="x", line=None, on_line=None)
        out = OutputMemberRef.from_ref(ref)
        assert out.line is None
        assert out.on_line is None

    def test_to_dict_required_fields(self):
        out = OutputMemberRef(
            target_name="getId",
            target_fqn="App\\User::getId()",
            target_kind="Method",
            file="src/User.php",
            line=11,
        )
        d = out.to_dict()
        assert d["target_name"] == "getId"
        assert d["target_fqn"] == "App\\User::getId()"
        assert d["target_kind"] == "Method"
        assert d["file"] == "src/User.php"
        assert d["line"] == 11

    def test_to_dict_conditional_fields(self):
        out = OutputMemberRef(
            target_name="x",
            target_fqn="x",
            target_kind=None,
            file=None,
            line=None,
            reference_type="method_call",
            access_chain="$this->repo",
            access_chain_symbol="sym:1",
            on_kind="property",
            on_file="f.php",
            on_line=5,
        )
        d = out.to_dict()
        assert d["reference_type"] == "method_call"
        assert d["access_chain"] == "$this->repo"
        assert d["access_chain_symbol"] == "sym:1"
        assert d["on_kind"] == "property"
        assert d["on_file"] == "f.php"
        assert d["on_line"] == 5


class TestOutputEntry:
    """Test OutputEntry conversion and serialization."""

    def test_line_number_conversion(self):
        entry = _make_context_entry(line=42)  # 0-based
        out = OutputEntry.from_entry(entry)
        assert out.line == 43  # 1-based

    def test_line_none_stays_none(self):
        entry = _make_context_entry(line=None)
        out = OutputEntry.from_entry(entry)
        assert out.line is None

    def test_to_dict_required_fields(self):
        entry = _make_context_entry()
        out = OutputEntry.from_entry(entry)
        d = out.to_dict()
        assert "depth" in d
        assert "fqn" in d
        assert "kind" in d
        assert "file" in d
        assert "line" in d
        assert "children" in d

    def test_to_dict_camel_case_ref_type(self):
        entry = _make_context_entry(ref_type="instantiation")
        out = OutputEntry.from_entry(entry)
        d = out.to_dict()
        assert d["refType"] == "instantiation"
        assert "ref_type" not in d

    def test_to_dict_camel_case_on_kind(self):
        entry = _make_context_entry(on_kind="property")
        out = OutputEntry.from_entry(entry)
        d = out.to_dict()
        assert d["onKind"] == "property"
        assert "on_kind" not in d

    def test_to_dict_property_name_as_property(self):
        entry = _make_context_entry(property_name="$email")
        out = OutputEntry.from_entry(entry)
        d = out.to_dict()
        assert d["property"] == "$email"
        assert "property_name" not in d

    def test_to_dict_access_count_camel_case(self):
        entry = _make_context_entry(access_count=5)
        out = OutputEntry.from_entry(entry)
        d = out.to_dict()
        assert d["accessCount"] == 5

    def test_to_dict_method_count_camel_case(self):
        entry = _make_context_entry(method_count=3)
        out = OutputEntry.from_entry(entry)
        d = out.to_dict()
        assert d["methodCount"] == 3

    def test_sites_removes_line(self):
        entry = _make_context_entry(sites=[{"line": 10}, {"line": 20}])
        out = OutputEntry.from_entry(entry)
        d = out.to_dict()
        assert "sites" in d
        assert "line" not in d

    def test_via_interface_flag(self):
        entry = _make_context_entry(via_interface=True)
        out = OutputEntry.from_entry(entry)
        d = out.to_dict()
        assert d["via_interface"] is True

    def test_via_interface_false_omitted(self):
        entry = _make_context_entry(via_interface=False)
        out = OutputEntry.from_entry(entry)
        d = out.to_dict()
        assert "via_interface" not in d


class TestOutputEntrySignatureRules:
    """Test mode-dependent signature inclusion."""

    def test_method_level_no_ref_type_includes_signature(self):
        entry = _make_context_entry(signature="process(): void")
        out = OutputEntry.from_entry(entry, class_level=False)
        assert out.signature == "process(): void"

    def test_method_level_with_ref_type_suppresses_signature(self):
        entry = _make_context_entry(signature="process(): void", ref_type="instantiation")
        out = OutputEntry.from_entry(entry, class_level=False)
        assert out.signature is None

    def test_method_level_override_ref_type_includes_signature(self):
        entry = _make_context_entry(signature="getId(): int", ref_type="override")
        out = OutputEntry.from_entry(entry, class_level=False)
        assert out.signature == "getId(): int"

    def test_method_level_inherited_ref_type_includes_signature(self):
        entry = _make_context_entry(signature="getId(): int", ref_type="inherited")
        out = OutputEntry.from_entry(entry, class_level=False)
        assert out.signature == "getId(): int"

    def test_class_level_no_ref_type_suppresses_signature(self):
        entry = _make_context_entry(signature="process(): void")
        out = OutputEntry.from_entry(entry, class_level=True)
        assert out.signature is None

    def test_class_level_override_includes_signature(self):
        entry = _make_context_entry(signature="getId(): int", ref_type="override")
        out = OutputEntry.from_entry(entry, class_level=True)
        assert out.signature == "getId(): int"

    def test_class_level_instantiation_suppresses_signature(self):
        entry = _make_context_entry(signature="process(): void", ref_type="instantiation")
        out = OutputEntry.from_entry(entry, class_level=True)
        assert out.signature is None


class TestOutputEntryMemberRefRules:
    """Test mode-dependent member_ref inclusion."""

    def test_method_level_no_ref_type_includes_member_ref(self):
        ref = MemberRef(target_name="getId", target_fqn="App\\User::getId()", line=10)
        entry = _make_context_entry(member_ref=ref)
        out = OutputEntry.from_entry(entry, class_level=False)
        assert out.member_ref is not None

    def test_method_level_with_ref_type_suppresses_member_ref(self):
        ref = MemberRef(target_name="getId", target_fqn="App\\User::getId()", line=10)
        entry = _make_context_entry(member_ref=ref, ref_type="method_call")
        out = OutputEntry.from_entry(entry, class_level=False)
        assert out.member_ref is None

    def test_class_level_suppresses_member_ref(self):
        ref = MemberRef(target_name="getId", target_fqn="App\\User::getId()", line=10)
        entry = _make_context_entry(member_ref=ref)
        out = OutputEntry.from_entry(entry, class_level=True)
        assert out.member_ref is None


class TestOutputEntryArgumentRules:
    """Test mode-dependent argument formatting."""

    def test_method_level_no_ref_type_uses_rich_arguments(self):
        args = [
            ArgumentInfo(position=0, param_name="$id", value_expr="42", value_source="literal"),
        ]
        entry = _make_context_entry(arguments=args)
        out = OutputEntry.from_entry(entry, class_level=False)
        assert out.arguments is not None
        assert out.args is None
        assert len(out.arguments) == 1

    def test_class_level_uses_flat_args(self):
        args = [
            ArgumentInfo(position=0, param_name="$id", value_expr="42", value_source="literal"),
        ]
        entry = _make_context_entry(arguments=args)
        out = OutputEntry.from_entry(entry, class_level=True)
        assert out.arguments is None
        assert out.args is not None
        assert out.args["$id"] == "42"

    def test_has_ref_type_uses_flat_args(self):
        args = [
            ArgumentInfo(
                position=0,
                param_name="$id",
                param_fqn="App\\Service\\Foo::$id",
                value_expr="42",
                value_source="literal",
            ),
        ]
        entry = _make_context_entry(arguments=args, ref_type="instantiation")
        out = OutputEntry.from_entry(entry, class_level=False)
        assert out.arguments is None
        assert out.args is not None
        assert "Foo::$id" in out.args

    def test_flat_args_missing_value_uses_question_mark(self):
        args = [
            ArgumentInfo(position=0, param_name="$x", value_expr=None, value_source=None),
        ]
        entry = _make_context_entry(arguments=args, ref_type="instantiation")
        out = OutputEntry.from_entry(entry, class_level=False)
        assert out.args["$x"] == "?"


class TestOutputEntryCrossedFromRules:
    """Test crossed_from suppression rules."""

    def test_method_level_always_includes_crossed_from(self):
        entry = _make_context_entry(crossed_from="App\\Caller::method()", depth=1)
        out = OutputEntry.from_entry(entry, class_level=False)
        assert out.crossed_from == "App\\Caller::method()"

    def test_class_level_depth_1_suppresses_crossed_from(self):
        entry = _make_context_entry(crossed_from="App\\Caller::method()", depth=1)
        out = OutputEntry.from_entry(entry, class_level=True)
        assert out.crossed_from is None

    def test_class_level_depth_2_includes_crossed_from(self):
        entry = _make_context_entry(crossed_from="App\\Caller::method()", depth=2)
        out = OutputEntry.from_entry(entry, class_level=True)
        assert out.crossed_from == "App\\Caller::method()"


class TestOutputEntryCalleeRules:
    """Test callee inclusion rules."""

    def test_callee_only_for_method_call(self):
        entry = _make_context_entry(callee="process()", ref_type="method_call")
        out = OutputEntry.from_entry(entry)
        assert out.callee == "process()"

    def test_callee_suppressed_for_other_ref_types(self):
        entry = _make_context_entry(callee="process()", ref_type="instantiation")
        out = OutputEntry.from_entry(entry)
        assert out.callee is None

    def test_callee_suppressed_when_no_ref_type(self):
        entry = _make_context_entry(callee="process()")
        out = OutputEntry.from_entry(entry)
        assert out.callee is None


class TestOutputEntryRecursive:
    """Test recursive conversion of children, implementations, source_call."""

    def test_children_converted_recursively(self):
        child = _make_context_entry(depth=2, line=50, fqn="App\\Inner::call()")
        entry = _make_context_entry(children=[child])
        out = OutputEntry.from_entry(entry)
        assert len(out.children) == 1
        assert out.children[0].line == 51  # 0->1 based
        assert out.children[0].fqn == "App\\Inner::call()"

    def test_implementations_converted(self):
        impl = _make_context_entry(depth=1, line=30, fqn="App\\Impl::execute()")
        entry = _make_context_entry(implementations=[impl])
        out = OutputEntry.from_entry(entry)
        assert out.implementations is not None
        assert len(out.implementations) == 1
        assert out.implementations[0].line == 31

    def test_source_call_converted(self):
        sc = _make_context_entry(depth=1, line=15, fqn="App\\Factory::create()")
        entry = _make_context_entry(source_call=sc)
        out = OutputEntry.from_entry(entry)
        assert out.source_call is not None
        assert out.source_call.line == 16


class TestOutputTarget:
    """Test OutputTarget conversion and serialization."""

    def test_from_node(self):
        node = _make_node(start_line=20)
        target = OutputTarget.from_node(node)
        assert target.fqn == "App\\Repo::findById()"
        assert target.line == 21  # 0->1 based
        assert target.file == "src/Repo.php"

    def test_from_node_none_line(self):
        node = _make_node(start_line=None)
        target = OutputTarget.from_node(node)
        assert target.line is None

    def test_to_dict_with_signature(self):
        node = _make_node(signature="findById(int $id): ?User")
        target = OutputTarget.from_node(node)
        d = target.to_dict()
        assert d["signature"] == "findById(int $id): ?User"

    def test_to_dict_without_signature(self):
        node = _make_node()
        target = OutputTarget.from_node(node)
        d = target.to_dict()
        assert "signature" not in d


class TestOutputDefinition:
    """Test OutputDefinition conversion and serialization."""

    def test_from_info_line_conversion(self):
        info = DefinitionInfo(fqn="App\\Foo", kind="Class", line=10)  # 0-based
        out = OutputDefinition.from_info(info)
        assert out.line == 11  # 1-based

    def test_from_info_none_line(self):
        info = DefinitionInfo(fqn="App\\Foo", kind="Class", line=None)
        out = OutputDefinition.from_info(info)
        assert out.line is None

    def test_property_type_extraction(self):
        info = DefinitionInfo(
            fqn="App\\Foo::$bar",
            kind="Property",
            return_type={
                "name": "string",
                "fqn": "string",
                "visibility": "private",
                "readonly": True,
                "promoted": True,
                "static": False,
            },
        )
        out = OutputDefinition.from_info(info)
        assert out.type_name == "string"
        assert out.visibility == "private"
        assert out.readonly is True
        assert out.promoted is True
        assert out.static is None  # False becomes None via `or None`
        assert out.return_type is None  # Not set for Property

    def test_property_to_dict_type_field(self):
        info = DefinitionInfo(
            fqn="App\\Foo::$bar",
            kind="Property",
            return_type={"name": "string", "visibility": "public"},
        )
        out = OutputDefinition.from_info(info)
        d = out.to_dict()
        assert d["type"] == "string"
        assert d["visibility"] == "public"
        assert "returnType" not in d

    def test_method_return_type(self):
        info = DefinitionInfo(
            fqn="App\\Foo::bar()",
            kind="Method",
            return_type={"fqn": "App\\User", "name": "User"},
        )
        out = OutputDefinition.from_info(info)
        d = out.to_dict()
        assert d["returnType"] == {"fqn": "App\\User", "name": "User"}
        assert "type" not in d

    def test_declared_in_line_conversion(self):
        info = DefinitionInfo(
            fqn="App\\Foo::bar()",
            kind="Method",
            declared_in={"fqn": "App\\Foo", "file": "f.php", "line": 5},
        )
        out = OutputDefinition.from_info(info)
        d = out.to_dict()
        assert d["declaredIn"]["line"] == 6  # 0->1 based

    def test_declared_in_suppressed_for_class(self):
        info = DefinitionInfo(
            fqn="App\\Foo",
            kind="Class",
            declared_in={"fqn": "src/Foo.php", "file": "src/Foo.php", "line": 0},
        )
        out = OutputDefinition.from_info(info)
        assert out.declared_in is None

    def test_to_dict_constructor_deps_camel_case(self):
        info = DefinitionInfo(
            fqn="App\\Foo",
            kind="Class",
            constructor_deps=[{"name": "$service", "type": "Service"}],
        )
        out = OutputDefinition.from_info(info)
        d = out.to_dict()
        assert d["constructorDeps"] == [{"name": "$service", "type": "Service"}]

    def test_value_definition(self):
        info = DefinitionInfo(
            fqn="val.0",
            kind="Value",
            value_kind="local",
            type_info={"fqn": "App\\User", "name": "User"},
            source={"call_fqn": "call.0", "method_fqn": "App\\Repo::find()"},
        )
        out = OutputDefinition.from_info(info)
        d = out.to_dict()
        assert d["value_kind"] == "local"
        assert d["type"] == {"fqn": "App\\User", "name": "User"}
        assert d["source"]["method_fqn"] == "App\\Repo::find()"

    def test_class_with_extends_implements(self):
        info = DefinitionInfo(
            fqn="App\\Foo",
            kind="Class",
            extends="App\\Base",
            implements=["App\\FooInterface"],
            uses_traits=["App\\LoggableTrait"],
        )
        out = OutputDefinition.from_info(info)
        d = out.to_dict()
        assert d["extends"] == "App\\Base"
        assert d["implements"] == ["App\\FooInterface"]
        assert d["uses_traits"] == ["App\\LoggableTrait"]

    def test_methods_and_properties(self):
        info = DefinitionInfo(
            fqn="App\\Foo",
            kind="Class",
            properties=[{"name": "$email", "type": "string"}],
            methods=[{"name": "getEmail", "signature": "getEmail(): string"}],
        )
        out = OutputDefinition.from_info(info)
        d = out.to_dict()
        assert d["properties"] == [{"name": "$email", "type": "string"}]
        assert d["methods"] == [{"name": "getEmail", "signature": "getEmail(): string"}]


class TestContextOutput:
    """Test ContextOutput full conversion pipeline."""

    def test_from_result_method_target(self):
        target = _make_node(kind="Method")
        entry = _make_context_entry(line=42)
        result = ContextResult(
            target=target,
            max_depth=2,
            used_by=[entry],
            uses=[],
        )
        output = ContextOutput.from_result(result)
        assert output.target.fqn == "App\\Repo::findById()"
        assert output.max_depth == 2
        assert len(output.used_by) == 1
        assert output.used_by[0].line == 43  # 0->1 based

    def test_from_result_class_target_uses_class_level(self):
        target = _make_node(kind="Class", fqn="App\\User")
        # With class_level=True, signature without ref_type should be suppressed
        entry = _make_context_entry(signature="process(): void")
        result = ContextResult(target=target, max_depth=1, used_by=[entry])
        output = ContextOutput.from_result(result)
        assert output.used_by[0].signature is None

    def test_to_dict_camel_case_keys(self):
        target = _make_node()
        result = ContextResult(target=target, max_depth=3, used_by=[], uses=[])
        output = ContextOutput.from_result(result)
        d = output.to_dict()
        assert "maxDepth" in d
        assert d["maxDepth"] == 3
        assert "usedBy" in d
        assert "uses" in d
        assert "target" in d

    def test_to_dict_with_definition(self):
        target = _make_node()
        definition = DefinitionInfo(
            fqn="App\\Repo::findById()",
            kind="Method",
            file="src/Repo.php",
            line=20,
        )
        result = ContextResult(
            target=target,
            max_depth=1,
            definition=definition,
        )
        output = ContextOutput.from_result(result)
        d = output.to_dict()
        assert "definition" in d
        assert d["definition"]["fqn"] == "App\\Repo::findById()"
        assert d["definition"]["line"] == 21  # 0->1 based

    def test_to_dict_no_definition(self):
        target = _make_node()
        result = ContextResult(target=target, max_depth=1)
        output = ContextOutput.from_result(result)
        d = output.to_dict()
        assert "definition" not in d

    def test_interface_target_uses_class_level(self):
        target = _make_node(kind="Interface", fqn="App\\FooInterface")
        entry = _make_context_entry(signature="process(): void")
        result = ContextResult(target=target, max_depth=1, used_by=[entry])
        output = ContextOutput.from_result(result)
        # Interface is class_level, so signature without ref_type is suppressed
        assert output.used_by[0].signature is None

    def test_property_target_uses_class_level(self):
        target = _make_node(kind="Property", fqn="App\\Foo::$bar")
        entry = _make_context_entry(crossed_from="App\\Method()", depth=1)
        result = ContextResult(target=target, max_depth=1, used_by=[entry])
        output = ContextOutput.from_result(result)
        # Property is class_level, depth 1 suppresses crossed_from
        assert output.used_by[0].crossed_from is None

    def test_full_round_trip(self):
        """Full conversion: ContextResult -> ContextOutput -> dict."""
        target = _make_node(kind="Method", start_line=20, signature="findById(int $id): ?User")
        used_by_entry = _make_context_entry(
            depth=1,
            line=42,
            ref_type="method_call",
            callee="findById()",
            on="$this->repo",
            on_kind="property",
        )
        uses_entry = _make_context_entry(
            depth=1,
            line=100,
            fqn="App\\Entity\\User",
            kind="Class",
            ref_type="instantiation",
        )
        definition = DefinitionInfo(
            fqn="App\\Repo::findById()",
            kind="Method",
            file="src/Repo.php",
            line=20,
            signature="findById(int $id): ?User",
            arguments=[{"name": "$id", "type": "int"}],
            return_type={"fqn": "App\\User", "name": "User"},
            declared_in={"fqn": "App\\Repo", "file": "src/Repo.php", "line": 5},
        )
        result = ContextResult(
            target=target,
            max_depth=2,
            used_by=[used_by_entry],
            uses=[uses_entry],
            definition=definition,
        )
        output = ContextOutput.from_result(result)
        d = output.to_dict()

        # Target
        assert d["target"]["fqn"] == "App\\Repo::findById()"
        assert d["target"]["line"] == 21
        assert d["target"]["signature"] == "findById(int $id): ?User"

        # MaxDepth
        assert d["maxDepth"] == 2

        # UsedBy
        assert len(d["usedBy"]) == 1
        ub = d["usedBy"][0]
        assert ub["line"] == 43
        assert ub["refType"] == "method_call"
        assert ub["callee"] == "findById()"
        assert ub["on"] == "$this->repo"
        assert ub["onKind"] == "property"

        # Uses
        assert len(d["uses"]) == 1
        u = d["uses"][0]
        assert u["refType"] == "instantiation"

        # Definition
        assert d["definition"]["line"] == 21
        assert d["definition"]["declaredIn"]["line"] == 6
        assert d["definition"]["returnType"] == {"fqn": "App\\User", "name": "User"}
