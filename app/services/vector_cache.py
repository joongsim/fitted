"""Semantic query cache using pgvector ANN search + S3 for candidate storage."""

import hashlib
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import numpy as np

from app.models.item import Item
from app.services.db_service import get_connection

logger = logging.getLogger(__name__)

_CACHE_TTL_HOURS = 24
_COSINE_THRESHOLD = 0.15  # cosine *distance* threshold; 0 = identical, 2 = opposite
# distance = 1 - similarity, so 0.15 ≈ similarity 0.85


async def lookup(
    query_embedding: np.ndarray,
    threshold: float = _COSINE_THRESHOLD,
) -> Optional[tuple[list[Item], str]]:
    """
    Search query_cache for a semantically similar prior query.

    pgvector's <=> operator returns cosine *distance* (not similarity).
    We look for entries where that distance is below our threshold —
    meaning the cached query was similar enough that its results are reusable.

    Args:
        query_embedding: 512-dim L2-normalized query vector.
        threshold: Cosine distance threshold. Lower = stricter.

    Returns:
        (items, cache_id) on HIT, or None on MISS.
    """
    embedding_list = query_embedding.tolist()

    async with get_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT cache_id, s3_key,
                       (query_embedding <=> %s::vector) AS cosine_distance
                FROM query_cache
                WHERE expires_at > NOW()
                  AND (query_embedding <=> %s::vector) < %s
                ORDER BY cosine_distance
                LIMIT 1
                """,
                (embedding_list, embedding_list, threshold),
            )
            row = await cur.fetchone()

    if not row:
        logger.debug("Vector cache MISS (no entry within threshold=%.3f)", threshold)
        return None

    cache_id, s3_key, distance = row
    logger.info(
        "Vector cache HIT: cache_id=%s cosine_distance=%.4f", cache_id, distance
    )

    items = await _load_items_from_s3(s3_key)
    if items is None:
        logger.warning("Cache HIT but S3 load failed for %s — treating as MISS", s3_key)
        return None

    return items, str(cache_id)


async def store(
    query_text: str,
    query_embedding: np.ndarray,
    items: list[Item],
    s3_client,
    bucket: str,
    ttl_hours: int = _CACHE_TTL_HOURS,
) -> Optional[str]:
    """
    Serialize items to S3 and upsert a query_cache row.

    Uses ON CONFLICT (query_hash) DO UPDATE so that re-running the same query
    refreshes the TTL instead of inserting a duplicate.

    Args:
        query_text: Original query string — used to compute the dedup hash.
        query_embedding: 512-dim L2-normalized query vector.
        items: Candidate Item list to cache.
        s3_client: Boto3 S3 client.
        bucket: S3 bucket name.
        ttl_hours: Cache entry lifetime in hours.

    Returns:
        cache_id string on success, or None if either the S3 write or DB upsert fails.
    """
    cache_id = str(uuid.uuid4())
    s3_key = f"cache/query/{cache_id}.json"
    expires_at = datetime.now(tz=timezone.utc) + timedelta(hours=ttl_hours)
    q_hash = hashlib.sha256(query_text.encode()).hexdigest()
    embedding_list = query_embedding.tolist()

    # Write candidate list to S3 first — if DB upsert fails, S3 is orphaned but harmless
    try:
        payload = json.dumps([_item_to_dict(item) for item in items])
        s3_client.put_object(
            Bucket=bucket,
            Key=s3_key,
            Body=payload.encode("utf-8"),
            ContentType="application/json",
        )
    except Exception:
        logger.error("Failed to write cache to S3 key=%s", s3_key, exc_info=True)
        return None

    try:
        async with get_connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO query_cache
                        (cache_id, query_hash, query_text, query_embedding, s3_key, expires_at)
                    VALUES (%s, %s, %s, %s::vector, %s, %s)
                    ON CONFLICT (query_hash) DO UPDATE SET
                        query_embedding = EXCLUDED.query_embedding,
                        s3_key          = EXCLUDED.s3_key,
                        expires_at      = EXCLUDED.expires_at
                    """,
                    (cache_id, q_hash, query_text, embedding_list, s3_key, expires_at),
                )
                await conn.commit()
    except Exception:
        logger.error("Failed to upsert query_cache row", exc_info=True)
        return None

    logger.info("Vector cache STORED: cache_id=%s expires=%s", cache_id, expires_at)
    return cache_id


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def _item_to_dict(item: Item) -> dict:
    """Convert an Item dataclass to a JSON-serializable dict."""
    return {
        "item_id": item.item_id,
        "domain": item.domain,
        "title": item.title,
        "price": item.price,
        "image_url": item.image_url,
        "product_url": item.product_url,
        "source": item.source,
        "embedding": item.embedding.tolist() if item.embedding is not None else None,
        "attributes": item.attributes,
    }


def _dict_to_item(d: dict) -> Item:
    """Reconstruct an Item dataclass from a plain dict (e.g. loaded from S3)."""
    embedding_raw = d.get("embedding")
    embedding = np.array(embedding_raw, dtype=np.float32) if embedding_raw else None
    return Item(
        item_id=d["item_id"],
        domain=d["domain"],
        title=d["title"],
        price=float(d["price"]),
        image_url=d.get("image_url") or "",
        product_url=d.get("product_url") or "",
        source=d.get("source") or "",
        embedding=embedding,
        attributes=d.get("attributes") or {},
    )


async def _load_items_from_s3(s3_key: str) -> Optional[list[Item]]:
    """
    Fetch a serialized Item list from S3 and deserialize it.

    Args:
        s3_key: S3 object key written by store().

    Returns:
        List of Item objects, or None if the fetch or parse fails.
    """
    import boto3

    from app.core.config import config

    try:
        s3 = boto3.client("s3")
        response = s3.get_object(Bucket=config.weather_bucket_name, Key=s3_key)
        raw = json.loads(response["Body"].read().decode("utf-8"))
        return [_dict_to_item(d) for d in raw]
    except Exception:
        logger.error("Failed to load items from S3 key=%s", s3_key, exc_info=True)
        return None
