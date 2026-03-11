"""Tests for UsageEntry, UsagesTreeResult, DepsEntry, DepsTreeResult models."""

from src.models.node import NodeData
from src.models.results import (
    UsageEntry,
    UsagesTreeResult,
    DepsEntry,
    DepsTreeResult,
)


def _make_node(**overrides) -> NodeData:
    """Create a test NodeData with defaults."""
    defaults = {
        "node_id": "test-node",
        "kind": "Class",
        "name": "TestClass",
        "fqn": "App\\TestClass",
        "symbol": "scip-php . . App/TestClass#",
        "file": "src/TestClass.php",
        "start_line": 10,
    }
    defaults.update(overrides)
    return NodeData(**defaults)


class TestUsageEntry:
    """Tests for UsageEntry dataclass."""

    def test_basic_to_dict(self):
        entry = UsageEntry(
            depth=1,
            node_id="source-1",
            fqn="App\\Service\\Foo",
            file="src/Service/Foo.php",
            line=42,
        )
        d = entry.to_dict()
        assert d["depth"] == 1
        assert d["node_id"] == "source-1"
        assert d["fqn"] == "App\\Service\\Foo"
        assert d["file"] == "src/Service/Foo.php"
        assert d["line"] == 43  # 0-based -> 1-based

    def test_to_dict_no_file(self):
        entry = UsageEntry(depth=1, node_id="s1", fqn="Foo")
        d = entry.to_dict()
        assert "file" not in d
        assert "line" not in d

    def test_to_dict_file_no_line(self):
        entry = UsageEntry(depth=1, node_id="s1", fqn="Foo", file="src/Foo.php")
        d = entry.to_dict()
        assert d["file"] == "src/Foo.php"
        assert "line" not in d

    def test_to_dict_line_zero(self):
        """Line 0 (0-based) should become line 1 in output."""
        entry = UsageEntry(depth=1, node_id="s1", fqn="Foo", file="f.php", line=0)
        d = entry.to_dict()
        assert d["line"] == 1

    def test_to_dict_with_children(self):
        child = UsageEntry(depth=2, node_id="s2", fqn="Bar", file="b.php", line=5)
        parent = UsageEntry(
            depth=1, node_id="s1", fqn="Foo", file="f.php", line=10,
            children=[child],
        )
        d = parent.to_dict()
        assert "children" in d
        assert len(d["children"]) == 1
        assert d["children"][0]["depth"] == 2
        assert d["children"][0]["line"] == 6  # 0-based -> 1-based

    def test_to_dict_empty_children_not_included(self):
        entry = UsageEntry(depth=1, node_id="s1", fqn="Foo", children=[])
        d = entry.to_dict()
        assert "children" not in d

    def test_nested_children(self):
        grandchild = UsageEntry(depth=3, node_id="s3", fqn="Baz")
        child = UsageEntry(depth=2, node_id="s2", fqn="Bar", children=[grandchild])
        parent = UsageEntry(depth=1, node_id="s1", fqn="Foo", children=[child])
        d = parent.to_dict()
        assert d["children"][0]["children"][0]["fqn"] == "Baz"
        assert d["children"][0]["children"][0]["depth"] == 3


class TestUsagesTreeResult:
    """Tests for UsagesTreeResult dataclass."""

    def test_basic_to_dict(self):
        target = _make_node()
        entry = UsageEntry(depth=1, node_id="s1", fqn="App\\Caller", file="c.php", line=20)
        result = UsagesTreeResult(target=target, max_depth=1, tree=[entry])
        d = result.to_dict()
        assert d["target"]["id"] == "test-node"
        assert d["target"]["kind"] == "Class"
        assert d["target"]["fqn"] == "App\\TestClass"
        assert d["target"]["file"] == "src/TestClass.php"
        assert d["target"]["line"] == 11  # 0-based -> 1-based
        assert d["max_depth"] == 1
        assert len(d["tree"]) == 1
        assert d["tree"][0]["fqn"] == "App\\Caller"

    def test_empty_tree(self):
        target = _make_node()
        result = UsagesTreeResult(target=target, max_depth=1, tree=[])
        d = result.to_dict()
        assert d["tree"] == []

    def test_target_no_start_line(self):
        target = _make_node(start_line=None)
        result = UsagesTreeResult(target=target, max_depth=1, tree=[])
        d = result.to_dict()
        assert d["target"]["line"] is None

    def test_target_no_file(self):
        target = _make_node(file=None, start_line=None)
        result = UsagesTreeResult(target=target, max_depth=2, tree=[])
        d = result.to_dict()
        assert d["target"]["file"] is None
        assert d["target"]["line"] is None
        assert d["max_depth"] == 2


class TestDepsEntry:
    """Tests for DepsEntry dataclass."""

    def test_basic_to_dict(self):
        entry = DepsEntry(
            depth=1,
            node_id="dep-1",
            fqn="App\\Entity\\Order",
            file="src/Entity/Order.php",
            line=5,
        )
        d = entry.to_dict()
        assert d["depth"] == 1
        assert d["node_id"] == "dep-1"
        assert d["fqn"] == "App\\Entity\\Order"
        assert d["file"] == "src/Entity/Order.php"
        assert d["line"] == 6  # 0-based -> 1-based

    def test_to_dict_no_location(self):
        entry = DepsEntry(depth=1, node_id="d1", fqn="Foo")
        d = entry.to_dict()
        assert "file" not in d
        assert "line" not in d

    def test_to_dict_with_children(self):
        child = DepsEntry(depth=2, node_id="d2", fqn="Bar")
        parent = DepsEntry(depth=1, node_id="d1", fqn="Foo", children=[child])
        d = parent.to_dict()
        assert "children" in d
        assert len(d["children"]) == 1


class TestDepsTreeResult:
    """Tests for DepsTreeResult dataclass."""

    def test_basic_to_dict(self):
        target = _make_node()
        entry = DepsEntry(depth=1, node_id="d1", fqn="App\\Dep", file="d.php", line=3)
        result = DepsTreeResult(target=target, max_depth=1, tree=[entry])
        d = result.to_dict()
        assert d["target"]["id"] == "test-node"
        assert d["target"]["kind"] == "Class"
        assert d["max_depth"] == 1
        assert len(d["tree"]) == 1
        assert d["tree"][0]["line"] == 4  # 0-based -> 1-based

    def test_empty_tree(self):
        target = _make_node()
        result = DepsTreeResult(target=target, max_depth=1, tree=[])
        d = result.to_dict()
        assert d["tree"] == []
