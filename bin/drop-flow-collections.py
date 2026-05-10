#!/usr/bin/env python
"""Drop the three stale flow_* Qdrant collections — one-time cleanup.

These collections were used by the now-removed flow enrichment pipeline:
  - flow_business_embeddings
  - flow_technical_embeddings
  - flow_search_embeddings

After the flows-kloc-inteligence rebuild they are obsolete. This script
deletes them idempotently (Qdrant 200s on missing collections) and is safe
to run multiple times.

Reads QDRANT_URL from the environment, defaulting to http://localhost:6333.
"""

from __future__ import annotations

import os
import sys

from qdrant_client import QdrantClient


STALE_COLLECTIONS = [
    "flow_business_embeddings",
    "flow_technical_embeddings",
    "flow_search_embeddings",
]


def main() -> int:
    url = os.environ.get("QDRANT_URL", "http://localhost:6333")
    api_key = os.environ.get("QDRANT_API_KEY") or None
    client = QdrantClient(url=url, api_key=api_key)

    print(f"Qdrant: {url}")
    failures = 0
    for name in STALE_COLLECTIONS:
        try:
            client.delete_collection(name)
            print(f"Dropped {name}")
        except Exception as exc:
            failures += 1
            print(f"Skip {name}: {exc}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
