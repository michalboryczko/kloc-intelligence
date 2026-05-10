"""Tests for code chunking."""

from src.ai.chunker import CodeChunker
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


class TestCodeChunkerMethod:
    def test_method_single_chunk(self):
        chunker = CodeChunker(max_tokens=8000)
        node = _make_node(kind="Method")
        source = "public function test() { return true; }"
        chunks = chunker.chunk_node(node, source)
        assert len(chunks) == 1
        assert chunks[0].chunk_index == 0
        assert chunks[0].total_chunks == 1
        assert chunks[0].content == source
        assert chunks[0].node_id == "node:test123"

    def test_method_truncation(self):
        chunker = CodeChunker(max_tokens=10)  # very small
        node = _make_node(kind="Method")
        source = "x" * 200
        chunks = chunker.chunk_node(node, source)
        assert len(chunks) == 1
        assert "truncated" in chunks[0].content
        assert len(chunks[0].content) < 200


class TestCodeChunkerClass:
    def test_small_class_single_chunk(self):
        chunker = CodeChunker(max_tokens=8000)
        node = _make_node(kind="Class", node_id="node:class1")
        source = "class Foo { public function bar() {} }"
        chunks = chunker.chunk_node(node, source)
        assert len(chunks) == 1
        assert chunks[0].content == source

    def test_large_class_multi_chunk(self):
        # Build a realistic multi-line class source
        lines = ["class Big {", "    private $a;", "    private $b;"]
        for i in range(40):
            lines.append(f"    // method {i} body line " + "x" * 50)
        lines.append("}")
        source = "\n".join(lines)
        # source is ~3000+ chars

        # max_tokens=500 -> 2000 chars max per chunk
        # Prefix is ~60 chars + margin 100 = ~160 overhead, leaving ~1840 for methods
        # Each method body is 500 chars, so 2 methods should fit but not 4
        chunker = CodeChunker(max_tokens=500)
        node = _make_node(
            kind="Class",
            node_id="node:class1",
            enclosing_start_line=0,
            start_line=0,
        )

        # Methods start at line 3 (after properties)
        m1 = _make_node(kind="Method", node_id="node:m1", enclosing_start_line=3, start_line=3)
        m2 = _make_node(kind="Method", node_id="node:m2", enclosing_start_line=15, start_line=15)
        m3 = _make_node(kind="Method", node_id="node:m3", enclosing_start_line=27, start_line=27)
        m4 = _make_node(kind="Method", node_id="node:m4", enclosing_start_line=35, start_line=35)
        method_sources = [
            (m1, "a" * 500),
            (m2, "b" * 500),
            (m3, "c" * 500),
            (m4, "d" * 500),
        ]

        chunks = chunker.chunk_node(node, source, method_sources)
        assert len(chunks) >= 2, f"Expected >= 2 chunks, got {len(chunks)}"
        for chunk in chunks:
            assert chunk.total_chunks == len(chunks)
            assert chunk.node_id == "node:class1"

    def test_large_class_no_methods_truncates(self):
        chunker = CodeChunker(max_tokens=25)
        node = _make_node(kind="Class")
        source = "x" * 500
        chunks = chunker.chunk_node(node, source)
        assert len(chunks) == 1
        assert "truncated" in chunks[0].content

    def test_token_estimate(self):
        chunker = CodeChunker(max_tokens=100)
        node = _make_node(kind="Method")
        source = "a" * 100
        chunks = chunker.chunk_node(node, source)
        assert chunks[0].token_estimate == 25
