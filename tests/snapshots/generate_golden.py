#!/usr/bin/env python3
"""Generate golden output files from kloc-cli for snapshot testing.

Reads corpus.yaml and runs each query against kloc-cli, saving JSON output
to tests/snapshots/golden/{query-id}.json.

Usage:
    python tests/snapshots/generate_golden.py [--kloc-cli PATH] [--sot-dir PATH]
    python tests/snapshots/generate_golden.py --command context  # only context queries
    python tests/snapshots/generate_golden.py --id class-order-d1  # single query

Requires kloc-cli to be installed/available.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

import yaml

SCRIPT_DIR = Path(__file__).parent
CORPUS_PATH = SCRIPT_DIR / "corpus.yaml"
GOLDEN_DIR = SCRIPT_DIR / "golden"
PROJECT_ROOT = SCRIPT_DIR.parent.parent.parent  # kloc/


def load_corpus() -> dict:
    """Load corpus.yaml."""
    with open(CORPUS_PATH) as f:
        return yaml.safe_load(f)


def build_command(query: dict, kloc_cli: str, sot_path: str) -> list[str]:
    """Build the kloc-cli command for a query."""
    cmd = [kloc_cli]
    command = query["command"]

    if command == "context":
        cmd.extend(["context", query["symbol"]])
        cmd.extend(["--sot", sot_path])
        cmd.extend(["--depth", str(query.get("depth", 2))])
        if query.get("impl"):
            cmd.append("--impl")
        cmd.append("--json")
    elif command == "resolve":
        cmd.extend(["resolve", query["symbol"]])
        cmd.extend(["--sot", sot_path])
        cmd.append("--json")
    elif command == "usages":
        cmd.extend(["usages", query["symbol"]])
        cmd.extend(["--sot", sot_path])
        cmd.append("--json")
    elif command == "deps":
        cmd.extend(["deps", query["symbol"]])
        cmd.extend(["--sot", sot_path])
        cmd.append("--json")
    elif command == "owners":
        cmd.extend(["owners", query["symbol"]])
        cmd.extend(["--sot", sot_path])
        cmd.append("--json")
    elif command == "inherit":
        cmd.extend(["inherit", query["symbol"]])
        cmd.extend(["--sot", sot_path])
        cmd.append("--json")
    elif command == "overrides":
        cmd.extend(["overrides", query["symbol"]])
        cmd.extend(["--sot", sot_path])
        cmd.append("--json")
    else:
        raise ValueError(f"Unknown command: {command}")

    return cmd


def run_query(query: dict, kloc_cli: str, sot_path: str) -> dict | None:
    """Run a single query and return parsed JSON output."""
    cmd = build_command(query, kloc_cli, sot_path)
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            print(f"  ERROR (exit {result.returncode}): {result.stderr.strip()[:200]}")
            return None
        return json.loads(result.stdout)
    except subprocess.TimeoutExpired:
        print("  ERROR: timeout (30s)")
        return None
    except json.JSONDecodeError as e:
        print(f"  ERROR: invalid JSON: {e}")
        return None


def main():
    parser = argparse.ArgumentParser(description="Generate golden outputs from kloc-cli")
    parser.add_argument(
        "--kloc-cli",
        default=str(PROJECT_ROOT / "kloc-cli" / ".venv" / "bin" / "kloc-cli"),
        help="Path to kloc-cli executable",
    )
    parser.add_argument(
        "--sot-dir",
        default=str(PROJECT_ROOT / "artifacts" / "kloc-dev"),
        help="Base directory for sot.json files",
    )
    parser.add_argument("--command", help="Only run queries for this command")
    parser.add_argument("--id", help="Only run a specific query by id")
    parser.add_argument("--dry-run", action="store_true", help="Print commands without running")
    args = parser.parse_args()

    corpus = load_corpus()
    sot_path = str(Path(args.sot_dir) / corpus["sot_id"] / "sot.json")

    queries = corpus["queries"]
    if args.command:
        queries = [q for q in queries if q["command"] == args.command]
    if args.id:
        queries = [q for q in queries if q["id"] == args.id]

    if not queries:
        print("No matching queries found.")
        sys.exit(1)

    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Generating golden output for {len(queries)} queries...")
    print(f"  kloc-cli: {args.kloc_cli}")
    print(f"  sot: {sot_path}")
    print()

    success = 0
    failed = 0
    skipped = 0

    for query in queries:
        qid = query["id"]
        if args.dry_run:
            cmd = build_command(query, args.kloc_cli, sot_path)
            print(f"  {qid}: {' '.join(cmd)}")
            continue

        print(f"  {qid}...", end=" ", flush=True)
        output = run_query(query, args.kloc_cli, sot_path)
        if output is None:
            failed += 1
            continue

        out_path = GOLDEN_DIR / f"{qid}.json"
        with open(out_path, "w") as f:
            json.dump(output, f, indent=2, sort_keys=True)
            f.write("\n")
        print("OK")
        success += 1

    if not args.dry_run:
        print(f"\nDone: {success} OK, {failed} failed, {skipped} skipped")


if __name__ == "__main__":
    main()
