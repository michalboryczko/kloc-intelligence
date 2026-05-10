"""Tests for source code reader."""

from src.ai.source_reader import SourceReader
from src.models.node import NodeData


def _make_node(**kwargs) -> NodeData:
    defaults = dict(
        node_id="node:test123",
        kind="Method",
        name="testMethod",
        fqn="App\\Test::testMethod()",
        symbol="scip-php test",
    )
    defaults.update(kwargs)
    return NodeData(**defaults)


class TestSourceReader:
    def test_read_method_source(self, tmp_path):
        php = tmp_path / "src" / "Test.php"
        php.parent.mkdir(parents=True)
        php.write_text(
            "<?php\n"
            "class Test {\n"
            "    public function hello(): string {\n"
            "        return 'world';\n"
            "    }\n"
            "}\n"
        )
        reader = SourceReader(str(tmp_path))
        node = _make_node(
            file="src/Test.php",
            enclosing_start_line=2,
            enclosing_end_line=4,
        )
        source = reader.read_node_source(node)
        assert source is not None
        assert "public function hello" in source
        assert "return 'world'" in source

    def test_read_class_source(self, tmp_path):
        php = tmp_path / "src" / "Order.php"
        php.parent.mkdir(parents=True)
        php.write_text(
            "<?php\n"
            "namespace App\\Entity;\n"
            "\n"
            "class Order {\n"
            "    private int $id;\n"
            "}\n"
        )
        reader = SourceReader(str(tmp_path))
        node = _make_node(
            kind="Class",
            file="src/Order.php",
            enclosing_start_line=3,
            enclosing_end_line=5,
        )
        source = reader.read_node_source(node)
        assert source is not None
        assert "class Order" in source

    def test_file_not_found(self, tmp_path):
        reader = SourceReader(str(tmp_path))
        node = _make_node(file="nonexistent.php", start_line=0, end_line=5)
        source = reader.read_node_source(node)
        assert source is None

    def test_no_file_field(self, tmp_path):
        reader = SourceReader(str(tmp_path))
        node = _make_node(file=None)
        source = reader.read_node_source(node)
        assert source is None

    def test_no_line_range(self, tmp_path):
        php = tmp_path / "test.php"
        php.write_text("<?php\n")
        reader = SourceReader(str(tmp_path))
        node = _make_node(file="test.php")
        source = reader.read_node_source(node)
        assert source is None

    def test_fallback_to_start_end_line(self, tmp_path):
        php = tmp_path / "test.php"
        php.write_text("line0\nline1\nline2\nline3\n")
        reader = SourceReader(str(tmp_path))
        node = _make_node(
            file="test.php",
            start_line=1,
            end_line=2,
        )
        source = reader.read_node_source(node)
        assert source is not None
        assert "line1" in source
        assert "line2" in source

    def test_estimate_tokens(self):
        assert SourceReader.estimate_tokens("a" * 400) == 100
        assert SourceReader.estimate_tokens("") == 0
