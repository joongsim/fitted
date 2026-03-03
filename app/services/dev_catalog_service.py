"""Dev catalog candidate source — pgvector ANN search on catalog_items."""

import logging

import numpy as np

from app.models.item import Item
from app.services.db_service import get_connection

logger = logging.getLogger(__name__)


async def search(
    query_embedding: np.ndarray,
    limit: int = 50,
    domain: str = "fashion",
) -> list[Item]:
    """
    Retrieve candidate Items from catalog_items via pgvector ANN search.

    Primary path: ORDER BY (embedding <=> query_embedding) — cosine distance,
    ascending (nearest first). Only rows where embedding IS NOT NULL are eligible.

    Fallback path: When no embedded rows exist (e.g., before the backfill script
    has run), fall back to returning the most recently seen items. This ensures
    the recommendation pipeline returns _something_ even on a fresh DB.

    Args:
        query_embedding: 512-dim L2-normalized query vector.
        limit: Maximum candidates to return.
        domain: Domain filter ('fashion', 'furniture', ...).

    Returns:
        List of Item objects. Order is by cosine similarity when embeddings
        exist, by recency otherwise.
    """
    embedding_list = query_embedding.tolist()

    async with get_connection() as conn:
        async with conn.cursor() as cur:
            # Primary: vector search on rows that have been embedded
            await cur.execute(
                """
                SELECT item_id, domain, title, price, image_url, product_url,
                       source, embedding, attributes,
                       (embedding <=> %s::vector) AS cosine_distance
                FROM catalog_items
                WHERE domain = %s
                  AND embedding IS NOT NULL
                ORDER BY cosine_distance
                LIMIT %s
                """,
                (embedding_list, domain, limit),
            )
            rows = await cur.fetchall()

            if not rows:
                logger.warning(
                    "No embedded catalog items for domain=%s — falling back to recency",
                    domain,
                )
                await cur.execute(
                    """
                    SELECT item_id, domain, title, price, image_url, product_url,
                           source, embedding, attributes, NULL AS cosine_distance
                    FROM catalog_items
                    WHERE domain = %s
                    ORDER BY last_seen DESC
                    LIMIT %s
                    """,
                    (domain, limit),
                )
                rows = await cur.fetchall()

    items = []
    for row in rows:
        (
            item_id,
            dom,
            title,
            price,
            image_url,
            product_url,
            source,
            emb_raw,
            attrs,
            _,
        ) = row
        if emb_raw is None:
            embedding = None
        elif isinstance(emb_raw, str):
            import json
            embedding = np.array(json.loads(emb_raw), dtype=np.float32)
        else:
            embedding = np.array(emb_raw, dtype=np.float32)
        items.append(
            Item(
                item_id=str(item_id),
                domain=dom or domain,
                title=title or "",
                price=float(price or 0.0),
                image_url=image_url or "",
                product_url=product_url or "",
                source=source or "",
                embedding=embedding,
                attributes=attrs or {},
            )
        )

    logger.info(
        "dev_catalog_service.search: domain=%s limit=%d -> %d candidates",
        domain,
        limit,
        len(items),
    )
    return items
