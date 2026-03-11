"""Generic query runner for Neo4j Cypher queries."""

import time
import logging
from typing import Any

from neo4j import Record

from .connection import Neo4jConnection

logger = logging.getLogger(__name__)


class QueryRunner:
    """Execute Cypher queries against Neo4j with logging and convenience methods."""

    def __init__(self, connection: Neo4jConnection):
        self._connection = connection

    def execute(self, query: str, **params) -> list[Record]:
        """Execute a Cypher query and return all result records."""
        start = time.perf_counter()
        with self._connection.session() as session:
            result = session.run(query, **params)
            records = list(result)
        elapsed = time.perf_counter() - start
        logger.debug(
            "Query %.1fms (%d records): %s",
            elapsed * 1000,
            len(records),
            query[:100],
        )
        return records

    def execute_single(self, query: str, **params) -> Record | None:
        """Execute a query and return the first record, or None."""
        records = self.execute(query, **params)
        return records[0] if records else None

    def execute_value(self, query: str, **params) -> Any:
        """Execute a query and return the first value of the first record."""
        record = self.execute_single(query, **params)
        return record[0] if record else None

    def execute_count(self, query: str, **params) -> int:
        """Execute a count query and return the integer result."""
        return self.execute_value(query, **params) or 0
