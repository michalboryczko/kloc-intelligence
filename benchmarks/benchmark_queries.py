#!/usr/bin/env python3
"""kloc-intelligence Performance Benchmarks.

Standalone benchmark script that measures query performance for all 8 command
types against a live Neo4j instance with loaded test data.

Usage:
    uv run python benchmarks/benchmark_queries.py
    uv run python benchmarks/benchmark_queries.py --iterations 20
    uv run python benchmarks/benchmark_queries.py --verbose

Requires:
    - Neo4j running with data loaded (skip gracefully if unavailable)
    - Test dataset: artifacts/kloc-dev/context-final/sot.json (1154 nodes, 2697 edges)
"""

import argparse
import statistics
import sys
import time
from pathlib import Path

# Add project root to path so we can import src modules
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def check_neo4j():
    """Check Neo4j connectivity, return (connection, runner) or (None, None)."""
    try:
        from src.config import Neo4jConfig
        from src.db.connection import Neo4jConnection, Neo4jConnectionError
        from src.db.query_runner import QueryRunner

        config = Neo4jConfig.from_env()
        conn = Neo4jConnection(config)
        conn.verify_connectivity()
        runner = QueryRunner(conn)
        return conn, runner
    except Exception as e:
        print(f"Neo4j not available: {e}")
        return None, None


def get_dataset_info(runner):
    """Get node and edge counts from the database."""
    node_count = runner.execute_value("MATCH (n:Node) RETURN count(n)")
    edge_count = runner.execute_value("MATCH ()-[r]->() RETURN count(r)")
    return node_count or 0, edge_count or 0


def ensure_data_loaded(conn, runner):
    """Ensure test data is loaded; import if empty."""
    node_count, edge_count = get_dataset_info(runner)
    if node_count >= 1000:
        return node_count, edge_count

    sot_path = PROJECT_ROOT.parent / "artifacts" / "kloc-dev" / "context-final" / "sot.json"
    if not sot_path.exists():
        print(f"Test dataset not found at {sot_path}")
        print("Cannot run benchmarks without data.")
        return 0, 0

    print(f"Loading test data from {sot_path}...")
    from src.db.importer import parse_sot, import_nodes, import_edges
    from src.db.schema import drop_all, ensure_schema

    drop_all(conn)
    ensure_schema(conn)
    nodes, edges = parse_sot(str(sot_path))
    import_nodes(conn, nodes)
    import_edges(conn, edges)
    return len(nodes), len(edges)


# ---------------------------------------------------------------------------
# Benchmark definitions
# ---------------------------------------------------------------------------

# Each benchmark: (label, callable_factory)
# callable_factory takes (runner,) and returns a zero-arg callable


def bench_resolve_class(runner):
    from src.db.queries.resolve import resolve_symbol
    return lambda: resolve_symbol(runner, "App\\Entity\\Order")


def bench_resolve_method(runner):
    from src.db.queries.resolve import resolve_symbol
    return lambda: resolve_symbol(runner, "App\\Entity\\Order::getTotal()")


def bench_resolve_interface(runner):
    from src.db.queries.resolve import resolve_symbol
    return lambda: resolve_symbol(runner, "App\\Component\\OrderProcessorInterface")


def bench_resolve_partial(runner):
    from src.db.queries.resolve import resolve_symbol
    return lambda: resolve_symbol(runner, "OrderController")


def bench_usages_class_d1(runner):
    from src.orchestration.usages import run_usages
    return lambda: run_usages(runner, "App\\Entity\\Order", depth=1, limit=100)


def bench_usages_method_d1(runner):
    from src.orchestration.usages import run_usages
    return lambda: run_usages(runner, "App\\Entity\\Order::getTotal()", depth=1, limit=100)


def bench_usages_class_d2(runner):
    from src.orchestration.usages import run_usages
    return lambda: run_usages(runner, "App\\Entity\\Order", depth=2, limit=100)


def bench_deps_class_d1(runner):
    from src.orchestration.deps import run_deps
    return lambda: run_deps(runner, "App\\Entity\\Order", depth=1, limit=100)


def bench_deps_method_d1(runner):
    from src.orchestration.deps import run_deps
    return lambda: run_deps(runner, "App\\Service\\OrderService::createOrder()", depth=1, limit=100)


def bench_deps_class_d2(runner):
    from src.orchestration.deps import run_deps
    return lambda: run_deps(runner, "App\\Entity\\Order", depth=2, limit=100)


def bench_context_class_d1(runner):
    from src.orchestration.context import execute_context
    return lambda: execute_context(runner, "App\\Entity\\Order", depth=1, limit=100)


def bench_context_method_d1(runner):
    from src.orchestration.context import execute_context
    return lambda: execute_context(
        runner, "App\\Service\\OrderService::createOrder()", depth=1, limit=100
    )


def bench_context_class_d2(runner):
    from src.orchestration.context import execute_context
    return lambda: execute_context(runner, "App\\Entity\\Order", depth=2, limit=100)


def bench_context_interface_d1(runner):
    from src.orchestration.context import execute_context
    return lambda: execute_context(
        runner, "App\\Component\\OrderProcessorInterface", depth=1, limit=100
    )


def bench_context_property_d1(runner):
    from src.orchestration.context import execute_context
    return lambda: execute_context(
        runner, "App\\Entity\\Order::$total", depth=1, limit=100
    )


def bench_owners_method(runner):
    from src.orchestration.simple import run_owners
    return lambda: run_owners(runner, "App\\Service\\OrderService::createOrder()")


def bench_owners_property(runner):
    from src.orchestration.simple import run_owners
    return lambda: run_owners(runner, "App\\Entity\\Order::$total")


def bench_inherit_class_up(runner):
    from src.orchestration.simple import run_inherit
    return lambda: run_inherit(
        runner, "App\\Service\\LoggingOrderProcessor", direction="up", depth=5, limit=100
    )


def bench_inherit_interface_down(runner):
    from src.orchestration.simple import run_inherit
    return lambda: run_inherit(
        runner, "App\\Component\\OrderProcessorInterface", direction="down", depth=5, limit=100
    )


def bench_overrides_up(runner):
    from src.orchestration.simple import run_overrides
    return lambda: run_overrides(
        runner, "App\\Service\\LoggingOrderProcessor::process()", direction="up", depth=5, limit=100
    )


def bench_overrides_down(runner):
    from src.orchestration.simple import run_overrides
    return lambda: run_overrides(
        runner, "App\\Component\\OrderProcessorInterface::process()", direction="down", depth=5, limit=100
    )


def bench_import_parse(runner):
    """Benchmark sot.json parsing only (no Neo4j write)."""
    from src.db.importer import parse_sot
    sot_path = str(PROJECT_ROOT.parent / "artifacts" / "kloc-dev" / "context-final" / "sot.json")
    return lambda: parse_sot(sot_path)


# Group definitions
BENCHMARK_GROUPS = {
    "resolve": [
        ("resolve class FQN", bench_resolve_class),
        ("resolve method FQN", bench_resolve_method),
        ("resolve interface FQN", bench_resolve_interface),
        ("resolve partial name", bench_resolve_partial),
    ],
    "usages": [
        ("usages class d=1", bench_usages_class_d1),
        ("usages method d=1", bench_usages_method_d1),
        ("usages class d=2", bench_usages_class_d2),
    ],
    "deps": [
        ("deps class d=1", bench_deps_class_d1),
        ("deps method d=1", bench_deps_method_d1),
        ("deps class d=2", bench_deps_class_d2),
    ],
    "context": [
        ("context class d=1", bench_context_class_d1),
        ("context method d=1", bench_context_method_d1),
        ("context class d=2", bench_context_class_d2),
        ("context interface d=1", bench_context_interface_d1),
        ("context property d=1", bench_context_property_d1),
    ],
    "owners": [
        ("owners method", bench_owners_method),
        ("owners property", bench_owners_property),
    ],
    "inherit": [
        ("inherit class up", bench_inherit_class_up),
        ("inherit interface down", bench_inherit_interface_down),
    ],
    "overrides": [
        ("overrides up", bench_overrides_up),
        ("overrides down", bench_overrides_down),
    ],
    "import": [
        ("import parse sot.json", bench_import_parse),
    ],
}


def run_benchmark(label, fn, iterations, verbose=False):
    """Run a single benchmark and return timing statistics."""
    timings = []

    # Warmup run
    try:
        fn()
    except Exception as e:
        return {"label": label, "error": str(e)}

    for i in range(iterations):
        start = time.perf_counter()
        try:
            fn()
        except Exception as e:
            return {"label": label, "error": str(e)}
        elapsed = (time.perf_counter() - start) * 1000  # ms
        timings.append(elapsed)
        if verbose:
            print(f"  iter {i + 1}: {elapsed:.2f}ms")

    return {
        "label": label,
        "min": min(timings),
        "max": max(timings),
        "mean": statistics.mean(timings),
        "stddev": statistics.stdev(timings) if len(timings) > 1 else 0,
        "median": statistics.median(timings),
        "iterations": iterations,
    }


def format_result(result):
    """Format a single benchmark result as a string."""
    if "error" in result:
        return f"  {result['label']:<40s}  ERROR: {result['error']}"

    return (
        f"  {result['label']:<40s}"
        f"  min={result['min']:>7.1f}ms"
        f"  max={result['max']:>7.1f}ms"
        f"  mean={result['mean']:>7.1f}ms"
        f"  stddev={result['stddev']:>6.1f}ms"
        f"  ({result['iterations']} iters)"
    )


def main():
    parser = argparse.ArgumentParser(description="kloc-intelligence Performance Benchmarks")
    parser.add_argument(
        "--iterations", "-n", type=int, default=10,
        help="Number of iterations per benchmark (default: 10)",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Show individual iteration timings",
    )
    parser.add_argument(
        "--group", "-g", type=str, default=None,
        help="Run only a specific group (resolve, usages, deps, context, owners, inherit, overrides, import)",
    )
    args = parser.parse_args()

    print()
    print("kloc-intelligence Performance Benchmarks")
    print("=" * 72)
    print()

    # Connect to Neo4j
    conn, runner = check_neo4j()
    if conn is None:
        print("Skipping benchmarks: Neo4j is not available.")
        print("Start Neo4j and load data first:")
        print("  docker compose up -d")
        print("  kloc-intelligence import artifacts/kloc-dev/context-final/sot.json")
        sys.exit(0)

    # Ensure data is loaded
    node_count, edge_count = ensure_data_loaded(conn, runner)
    if node_count == 0:
        print("No data in database. Cannot run benchmarks.")
        conn.close()
        sys.exit(1)

    print(f"Dataset: {node_count:,} nodes, {edge_count:,} edges")
    print(f"Iterations per benchmark: {args.iterations}")
    print()

    # Select groups
    if args.group:
        if args.group not in BENCHMARK_GROUPS:
            print(f"Unknown group: {args.group}")
            print(f"Available: {', '.join(BENCHMARK_GROUPS.keys())}")
            conn.close()
            sys.exit(1)
        groups = {args.group: BENCHMARK_GROUPS[args.group]}
    else:
        groups = BENCHMARK_GROUPS

    all_results = []
    total_start = time.perf_counter()

    for group_name, benchmarks in groups.items():
        print(f"--- {group_name} ---")
        for label, factory in benchmarks:
            try:
                fn = factory(runner)
            except Exception as e:
                result = {"label": label, "error": f"Setup failed: {e}"}
                print(format_result(result))
                all_results.append(result)
                continue

            result = run_benchmark(label, fn, args.iterations, verbose=args.verbose)
            print(format_result(result))
            all_results.append(result)
        print()

    total_time = time.perf_counter() - total_start

    # Summary
    successful = [r for r in all_results if "error" not in r]
    failed = [r for r in all_results if "error" in r]

    print("=" * 72)
    print(f"Total benchmarks: {len(all_results)}")
    print(f"  Successful: {len(successful)}")
    if failed:
        print(f"  Failed: {len(failed)}")
        for r in failed:
            print(f"    - {r['label']}: {r['error']}")

    if successful:
        fastest = min(successful, key=lambda r: r["mean"])
        slowest = max(successful, key=lambda r: r["mean"])
        print(f"\n  Fastest: {fastest['label']} ({fastest['mean']:.1f}ms mean)")
        print(f"  Slowest: {slowest['label']} ({slowest['mean']:.1f}ms mean)")

    print(f"\nTotal benchmark time: {total_time:.1f}s")

    conn.close()


if __name__ == "__main__":
    main()
