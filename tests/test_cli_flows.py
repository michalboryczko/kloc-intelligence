"""Smoke test for the CLI command list.

Verifies the post-flow-demolition surface: the legacy `flow-diagram` and
`explain-flow` commands are gone, and `import-flows` is still present.
"""

from typer.testing import CliRunner

from src.cli import app

REMOVED_COMMANDS = ["flow-diagram", "explain-flow"]
KEPT_COMMANDS = ["import-flows", "flows", "enrich-flows"]


def test_kloc_help_does_not_list_removed_commands():
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0, f"--help failed: {result.output}"

    output = result.output
    for cmd in REMOVED_COMMANDS:
        assert cmd not in output, f"Removed command {cmd!r} still appears in `kloc --help` output"

    for cmd in KEPT_COMMANDS:
        assert cmd in output, f"Expected command {cmd!r} missing from `kloc --help` output"
