#!/usr/bin/env bash
set -euo pipefail

if [ $# -lt 1 ]; then
    echo "Usage: $0 <path-to-sot.json>"
    exit 1
fi

SOT_FILE="$1"

echo "=== kloc-intelligence import ==="
echo "Importing: $SOT_FILE"

kloc-intelligence import "$SOT_FILE"

echo "=== Import complete ==="
