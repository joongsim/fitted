import logging
from typing import Optional

from app.services.db_service import get_connection

logger = logging.getLogger(__name__)


async def create_wardrobe_item(
    user_id: str,
    name: str,
    category: Optional[str],
    image_s3_key: Optional[str],
) -> dict:
    """
    Insert a new row into ``wardrobe_items`` and return it as a plain dict.

    The embedding and classification columns are left NULL — they are populated
    later by the embedding backfill script and the vision service respectively.

    Args:
        user_id: UUID of the owning user.
        name: Display name for the item (e.g. "Navy Blazer").
        category: Optional category string (tops / bottoms / outerwear / shoes / accessories).
        image_s3_key: S3 key of the uploaded image, or None if no image was provided.

    Returns:
        Dict with item_id, name, category, image_s3_key, tags, created_at.
    """
    async with get_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO wardrobe_items (user_id, name, category, image_s3_key)
                VALUES (%s, %s, %s, %s)
                RETURNING item_id, name, category, image_s3_key, tags, created_at
                """,
                (user_id, name, category, image_s3_key),
            )
            row = await cur.fetchone()
            await conn.commit()

    item_id, name_, cat, s3_key, tags, created_at = row
    logger.info(
        "wardrobe_service.create: user_id=%s item_id=%s name=%r category=%s",
        user_id,
        item_id,
        name_,
        cat,
    )
    return {
        "item_id": str(item_id),
        "name": name_,
        "category": cat,
        "image_s3_key": s3_key,
        "tags": list(tags) if tags else [],
        "created_at": created_at,
    }


async def get_wardrobe_items(user_id: str) -> list[dict]:
    """
    Return all wardrobe items owned by ``user_id``, ordered by creation date (newest first).

    Args:
        user_id: UUID of the user.

    Returns:
        List of dicts, each with item_id, name, category, image_s3_key, tags, created_at.
        Empty list if the user has no wardrobe items.
    """
    async with get_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT item_id, name, category, image_s3_key, tags, created_at
                FROM wardrobe_items
                WHERE user_id = %s
                ORDER BY created_at DESC
                """,
                (user_id,),
            )
            rows = await cur.fetchall()

    items = []
    for item_id, name, cat, s3_key, tags, created_at in rows:
        items.append(
            {
                "item_id": str(item_id),
                "name": name,
                "category": cat,
                "image_s3_key": s3_key,
                "tags": list(tags) if tags else [],
                "created_at": created_at,
            }
        )

    logger.debug(
        "wardrobe_service.get_wardrobe_items: user_id=%s -> %d items",
        user_id,
        len(items),
    )
    return items


async def get_wardrobe_item(user_id: str, item_id: str) -> Optional[dict]:
    """
    Fetch a single wardrobe item by ID, enforcing ownership.

    Args:
        user_id: UUID of the requesting user.
        item_id: UUID of the wardrobe item.

    Returns:
        Dict on success, ``None`` if not found or not owned by ``user_id``.
    """
    async with get_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT item_id, name, category, image_s3_key, tags, created_at
                FROM wardrobe_items
                WHERE item_id = %s AND user_id = %s
                """,
                (item_id, user_id),
            )
            row = await cur.fetchone()

    if row is None:
        logger.debug(
            "wardrobe_service.get_wardrobe_item: not found item_id=%s user_id=%s",
            item_id,
            user_id,
        )
        return None

    rid, name, cat, s3_key, tags, created_at = row
    return {
        "item_id": str(rid),
        "name": name,
        "category": cat,
        "image_s3_key": s3_key,
        "tags": list(tags) if tags else [],
        "created_at": created_at,
    }


async def delete_wardrobe_item(user_id: str, item_id: str) -> bool:
    """
    Delete a wardrobe item, enforcing ownership.

    The ``WHERE user_id = %s`` clause means a user cannot delete another user's
    items even if they supply the correct item_id — the same IDOR protection
    pattern used in the recommendation endpoint.

    Args:
        user_id: UUID of the requesting user.
        item_id: UUID of the wardrobe item to delete.

    Returns:
        ``True`` if a row was deleted, ``False`` if the item was not found or
        is not owned by the requesting user.
    """
    async with get_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                DELETE FROM wardrobe_items
                WHERE item_id = %s AND user_id = %s
                """,
                (item_id, user_id),
            )
            deleted = cur.rowcount
            await conn.commit()

    if deleted:
        logger.info(
            "wardrobe_service.delete: item_id=%s deleted by user_id=%s",
            item_id,
            user_id,
        )
    else:
        logger.debug(
            "wardrobe_service.delete: item_id=%s not found for user_id=%s",
            item_id,
            user_id,
        )
    return bool(deleted)


async def update_wardrobe_item(
    user_id: str,
    item_id: str,
    name: Optional[str] = None,
    category: Optional[str] = None,
    tags: Optional[list[str]] = None,
) -> Optional[dict]:
    """
    Update mutable metadata on a wardrobe item, enforcing ownership.

    Only the fields explicitly passed (non-None) are updated; omitted fields
    retain their current database values.  Returns the updated row as a dict,
    or ``None`` if the item does not exist or is not owned by ``user_id``.

    Args:
        user_id: UUID of the requesting user.
        item_id: UUID of the wardrobe item to update.
        name: New display name, or None to leave unchanged.
        category: New category, or None to leave unchanged.
        tags: New tag list, or None to leave unchanged.

    Returns:
        Dict with item_id, name, category, image_s3_key, tags, created_at on
        success; ``None`` if the item was not found or is not owned by the user.
    """
    set_clauses: list[str] = []
    params: list = []

    if name is not None:
        set_clauses.append("name = %s")
        params.append(name)
    if category is not None:
        set_clauses.append("category = %s")
        params.append(category)
    if tags is not None:
        set_clauses.append("tags = %s")
        params.append(tags)

    if not set_clauses:
        return await get_wardrobe_item(user_id, item_id)

    sql = f"""
        UPDATE wardrobe_items
        SET {', '.join(set_clauses)}
        WHERE item_id = %s AND user_id = %s
        RETURNING item_id, name, category, image_s3_key, tags, created_at
    """
    params.extend([item_id, user_id])

    async with get_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(sql, params)
            row = await cur.fetchone()
            await conn.commit()

    if row is None:
        logger.debug(
            "wardrobe_service.update: item_id=%s not found for user_id=%s",
            item_id,
            user_id,
        )
        return None

    rid, name_, cat, s3_key, tags_, created_at = row
    logger.info(
        "wardrobe_service.update: user_id=%s item_id=%s name=%r category=%s",
        user_id,
        rid,
        name_,
        cat,
    )
    return {
        "item_id": str(rid),
        "name": name_,
        "category": cat,
        "image_s3_key": s3_key,
        "tags": list(tags_) if tags_ else [],
        "created_at": created_at,
    }
