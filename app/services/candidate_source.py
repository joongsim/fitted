"""CandidateSource factory — routes to dev catalog or (future) live search."""

import logging
import os
from typing import Optional

import numpy as np

from app.models.item import Item
from app.services import dev_catalog_service

logger = logging.getLogger(__name__)


async def get_candidates(
    query_embedding: np.ndarray,
    limit: int = 50,
    domain: str = "fashion",
    cache_id: Optional[str] = None,
) -> list[Item]:
    """
    Return candidate Items for ranking.

    Routing logic (checked in order):
    1. DEV_MODE=true → dev catalog ANN search on catalog_items.
    2. Otherwise → vector cache HIT is already resolved by the caller;
       this function handles the MISS path (live search, not yet implemented).

    The vector cache lookup happens *before* this function is called in the
    recommendation pipeline — the caller passes cache_id=None on a MISS, at
    which point we need to fetch fresh candidates.

    Args:
        query_embedding: 512-dim L2-normalized query vector.
        limit: Maximum candidates to return.
        domain: Domain filter.
        cache_id: Set by the caller when a cache HIT provides candidates —
            unused here but kept for interface symmetry.

    Returns:
        List of Item objects.
    """
    dev_mode = os.environ.get("DEV_MODE", "false").lower() == "true"

    if dev_mode:
        logger.debug(
            "CandidateSource: DEV_MODE — routing to dev_catalog_service (domain=%s)",
            domain,
        )
        return await dev_catalog_service.search(
            query_embedding, limit=limit, domain=domain
        )

    # Production path: live search via SerpAPI (Week 5C, not yet implemented)
    logger.warning(
        "CandidateSource: production path not yet implemented — returning empty list"
    )
    return []
