#!/usr/bin/env bash
set -euo pipefail

echo "=== kloc-intelligence reset ==="

echo "Resetting schema and data..."
kloc-intelligence schema reset

echo "=== Reset complete ==="
