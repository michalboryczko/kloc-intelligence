from neo4j import GraphDatabase, Driver, Session
from ..config import Neo4jConfig


class Neo4jConnectionError(Exception):
    pass


class Neo4jConnection:
    def __init__(self, config: Neo4jConfig | None = None):
        self._config = config or Neo4jConfig.from_env()
        try:
            self._driver: Driver = GraphDatabase.driver(
                self._config.uri,
                auth=(self._config.username, self._config.password),
                max_connection_pool_size=self._config.max_connection_pool_size,
                connection_acquisition_timeout=self._config.connection_acquisition_timeout,
            )
        except Exception as e:
            raise Neo4jConnectionError(f"Failed to create Neo4j driver: {e}") from e

    def verify_connectivity(self) -> None:
        try:
            self._driver.verify_connectivity()
        except Exception as e:
            raise Neo4jConnectionError(f"Cannot connect to Neo4j at {self._config.uri}: {e}") from e

    def session(self, **kwargs) -> Session:
        return self._driver.session(database=self._config.database, **kwargs)

    def close(self) -> None:
        self._driver.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
