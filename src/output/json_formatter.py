"""JSON output helper for usages and deps results."""

import json
from typing import Any


def to_json(data: Any, indent: int = 2) -> str:
    """Convert data to formatted JSON string."""
    return json.dumps(data, indent=indent, ensure_ascii=False)


def print_json(data: Any, indent: int = 2) -> None:
    """Print data as formatted JSON to stdout."""
    print(to_json(data, indent=indent))
