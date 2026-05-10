"""Qdrant vector store for kloc-intelligence AI features."""

import uuid
from dataclasses import dataclass

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PointStruct,
    VectorParams,
)


COLLECTIONS = {
    "code_embeddings": "Source code embeddings for Class/Method nodes",
    "explain_embeddings": "Human-language explanation embeddings",
    "flow_business_embeddings": "Flow business process descriptions",
    "flow_technical_embeddings": "Flow technical architecture descriptions",
    "flow_search_embeddings": "Flow search descriptions for AI agents",
}


def _get_meta(payload: dict, key: str, default=None):
    """Get a value from payload, checking both top-level and Haystack's meta dict."""
    if key in payload:
        return payload[key]
    meta = payload.get("meta", {})
    if meta and key in meta:
        return meta[key]
    return default


def _point_id(node_id: str, chunk_index: int = 0) -> str:
    """Deterministic UUID5 from node_id:chunk_index for idempotent upserts."""
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{node_id}:{chunk_index}"))


@dataclass
class SearchHit:
    score: float
    node_id: str
    kind: str
    fqn: str
    name: str
    file: str | None
    project: str
    chunk_index: int
    collection: str


class QdrantStore:
    """Qdrant client wrapper for kloc-intelligence vector collections."""

    def __init__(self, url: str = "http://localhost:6333", api_key: str | None = None):
        self._url = url
        self._client = QdrantClient(url=url, api_key=api_key or None)

    def ensure_collections(self, dimension: int = 4096) -> dict[str, bool]:
        """Create collections if they don't exist. Returns {name: created}."""
        results = {}
        existing = {c.name for c in self._client.get_collections().collections}
        for name in COLLECTIONS:
            if name in existing:
                results[name] = False
            else:
                self._client.create_collection(
                    collection_name=name,
                    vectors_config=VectorParams(size=dimension, distance=Distance.COSINE),
                )
                results[name] = True
        return results

    def upsert_points(
        self,
        collection: str,
        vectors: list[list[float]],
        payloads: list[dict],
    ) -> int:
        """Upsert vectors with payloads. Each payload must have 'node_id' and 'chunk_index'."""
        points = []
        for vector, payload in zip(vectors, payloads):
            point_id = _point_id(payload["node_id"], payload.get("chunk_index", 0))
            points.append(PointStruct(id=point_id, vector=vector, payload=payload))
        if points:
            self._client.upsert(collection_name=collection, points=points)
        return len(points)

    def search(
        self,
        collection: str,
        query_vector: list[float],
        limit: int = 10,
        project: str | None = None,
        kind: str | None = None,
    ) -> list[SearchHit]:
        """Search a collection with optional payload filtering."""
        conditions = []
        if project:
            conditions.append(FieldCondition(key="project", match=MatchValue(value=project)))
        if kind:
            conditions.append(FieldCondition(key="kind", match=MatchValue(value=kind)))
        query_filter = Filter(must=conditions) if conditions else None

        results = self._client.search(
            collection_name=collection,
            query_vector=query_vector,
            query_filter=query_filter,
            limit=limit,
        )
        return [
            SearchHit(
                score=r.score,
                node_id=_get_meta(r.payload, "node_id", ""),
                kind=_get_meta(r.payload, "kind", ""),
                fqn=_get_meta(r.payload, "fqn", ""),
                name=_get_meta(r.payload, "name", ""),
                file=_get_meta(r.payload, "file"),
                project=_get_meta(r.payload, "project", ""),
                chunk_index=_get_meta(r.payload, "chunk_index", 0),
                collection=collection,
            )
            for r in results
        ]

    def get_by_node_id(self, collection: str, node_id: str) -> list[dict]:
        """Get all points for a node_id (may have multiple chunks).

        Checks both top-level and Haystack's meta.node_id path.
        """
        # Haystack stores metadata under payload.meta.*
        results, _ = self._client.scroll(
            collection_name=collection,
            scroll_filter=Filter(
                must=[FieldCondition(key="meta.node_id", match=MatchValue(value=node_id))]
            ),
            limit=100,
        )
        if not results:
            # Fallback: check top-level payload.node_id
            results, _ = self._client.scroll(
                collection_name=collection,
                scroll_filter=Filter(
                    must=[FieldCondition(key="node_id", match=MatchValue(value=node_id))]
                ),
                limit=100,
            )
        return [{"id": r.id, "payload": r.payload} for r in results]

    def delete_by_project(self, project: str) -> None:
        """Delete all points for a project from all collections."""
        for collection in COLLECTIONS:
            try:
                self._client.delete(
                    collection_name=collection,
                    points_selector=Filter(
                        must=[FieldCondition(key="meta.project", match=MatchValue(value=project))]
                    ),
                )
            except Exception:
                pass

    def delete_collections(self) -> None:
        """Delete all AI collections (used during reimport)."""
        for name in COLLECTIONS:
            try:
                self._client.delete_collection(name)
            except Exception:
                pass

    def collection_count(self, collection: str) -> int:
        """Get point count for a collection."""
        try:
            info = self._client.get_collection(collection)
            return info.points_count or 0
        except Exception:
            return 0

    def close(self) -> None:
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
