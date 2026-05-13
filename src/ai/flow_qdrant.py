"""Per-flow Qdrant point utilities for the ``flow_explain_embeddings`` collection.

The v3 import path must NEVER drop the collection — it filter-deletes points
whose payload ``flow_id`` matches an orphan :Flow node so other flows'
embeddings survive a re-import. ``list_flow_point_ids`` is the symmetric
inspection helper QA uses to assert pre/post counts.
"""

import logging

logger = logging.getLogger(__name__)

FLOW_EXPLAIN_COLLECTION = "flow_explain_embeddings"
_SCROLL_PAGE_LIMIT = 10_000


def _build_flow_id_filter(flow_id: str):
    from qdrant_client.http.models import FieldCondition, Filter, MatchValue

    return Filter(must=[FieldCondition(key="flow_id", match=MatchValue(value=flow_id))])


def _collection_missing(exc: BaseException) -> bool:
    """True if `exc` indicates the flow_explain_embeddings collection is absent.

    Qdrant raises ``UnexpectedResponse`` (HTTP 404) for missing collections; the
    text path can also raise ``ValueError`` in the local client.
    """
    msg = str(exc).lower()
    return "not found" in msg or "doesn't exist" in msg or "does not exist" in msg or "404" in msg


def delete_flow_embedding(
    flow_id: str,
    qdrant_url: str,
    qdrant_api_key: str | None = None,
) -> int:
    """Filter-delete points whose payload ``flow_id`` matches ``flow_id``.

    Returns the count of points that were present before the delete (best-effort
    via :func:`list_flow_point_ids`). Idempotent: missing collection or zero
    matches both return ``0`` and log a single WARNING for the missing
    collection. **Never** drops the collection.
    """
    from qdrant_client import QdrantClient
    from qdrant_client.http.models import FilterSelector

    client = QdrantClient(url=qdrant_url, api_key=qdrant_api_key)
    try:
        flt = _build_flow_id_filter(flow_id)
        try:
            point_ids = list_flow_point_ids(flow_id, qdrant_url, qdrant_api_key)
        except Exception:
            point_ids = []
        try:
            client.delete(
                collection_name=FLOW_EXPLAIN_COLLECTION,
                points_selector=FilterSelector(filter=flt),
            )
        except Exception as exc:
            if _collection_missing(exc):
                logger.warning(
                    "Qdrant collection %s missing — skipping delete for flow %s",
                    FLOW_EXPLAIN_COLLECTION,
                    flow_id,
                )
                return 0
            logger.warning("Qdrant delete for flow %s failed: %s", flow_id, exc)
            return 0
        return len(point_ids)
    finally:
        try:
            client.close()
        except Exception:
            pass


def list_flow_point_ids(
    flow_id: str,
    qdrant_url: str,
    qdrant_api_key: str | None = None,
) -> list[str | int]:
    """Return point IDs in ``flow_explain_embeddings`` whose payload ``flow_id`` matches.

    Used by QA's idempotency assertions. Returns an empty list when the
    collection is absent (logged WARNING).
    """
    from qdrant_client import QdrantClient

    client = QdrantClient(url=qdrant_url, api_key=qdrant_api_key)
    try:
        flt = _build_flow_id_filter(flow_id)
        try:
            points, _ = client.scroll(
                collection_name=FLOW_EXPLAIN_COLLECTION,
                scroll_filter=flt,
                with_payload=False,
                with_vectors=False,
                limit=_SCROLL_PAGE_LIMIT,
            )
        except Exception as exc:
            if _collection_missing(exc):
                logger.warning(
                    "Qdrant collection %s missing — returning empty point list for flow %s",
                    FLOW_EXPLAIN_COLLECTION,
                    flow_id,
                )
                return []
            logger.warning("Qdrant scroll for flow %s failed: %s", flow_id, exc)
            return []
        return [p.id for p in points]
    finally:
        try:
            client.close()
        except Exception:
            pass
