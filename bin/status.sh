#!/usr/bin/env bash
set -euo pipefail

echo "=== kloc-intelligence status ==="

echo "Neo4j container:"
docker ps --filter name=kloc-neo4j --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" 2>/dev/null || echo "  Docker not available"

echo ""
echo "Schema status:"
kloc-intelligence schema verify

echo "=== Status complete ==="
