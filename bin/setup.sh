#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "=== kloc-intelligence setup ==="

# Start Neo4j
echo "Starting Neo4j..."
cd "$PROJECT_DIR"
docker compose up -d

echo "Waiting for Neo4j to be ready..."
until docker compose exec neo4j neo4j status 2>/dev/null; do
    sleep 2
done

# Install Python dependencies
echo "Installing Python dependencies..."
pip install -e ".[dev]"

# Ensure schema
echo "Ensuring schema..."
kloc-intelligence schema ensure

echo "=== Setup complete ==="
