"""Smoke test for the CLI command list after the flow demolition (AC-8).

Verifies:
- `kloc --help` no longer lists `flow-diagram`, `explain-flow`, `enrich-flows`
- `import-flows` IS still listed
"""

from typer.testing import CliRunner

from src.cli import app


REMOVED_COMMANDS = ["flow-diagram", "explain-flow", "enrich-flows"]
KEPT_COMMAND = "import-flows"


def test_kloc_help_does_not_list_removed_commands():
    """AC-8: removed flow commands must not appear in --help; import-flows must remain."""
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0, f"--help failed: {result.output}"

    output = result.output
    for cmd in REMOVED_COMMANDS:
        assert cmd not in output, (
            f"Removed command {cmd!r} still appears in `kloc --help` output"
        )

    assert KEPT_COMMAND in output, (
        f"Expected command {KEPT_COMMAND!r} missing from `kloc --help` output"
    )
