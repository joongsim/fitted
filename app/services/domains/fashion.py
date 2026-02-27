"""FashionDomain — concrete Domain implementation for the fashion vertical."""

import logging

import numpy as np

from app.models.item import Item
from app.services.embedding_service import encode_text

logger = logging.getLogger(__name__)


class FashionDomain:
    """Fashion vertical domain implementation."""

    def encode_query(self, inputs: dict) -> np.ndarray:
        """
        Build a composite query string from weather + style context, then encode it.

        We don't just embed the raw search query text. We build a richer composite
        that also includes weather and style context — this pushes the query vector
        closer to items that are appropriate for the conditions, not just
        textually similar.

        Example composite:
            "navy blazer men. weather: sunny 22°C. colors: navy, white. style: smart casual"

        Args:
            inputs: Dict with keys query_text (str), weather_context (str),
                    style_preferences (dict with optional keys colors and styles).

        Returns:
            512-dim float32 unit vector.
        """
        query_text: str = inputs.get("query_text", "")
        weather_ctx: str = inputs.get("weather_context", "")
        style_prefs: dict = inputs.get("style_preferences", {})

        parts = [query_text]
        if weather_ctx:
            parts.append(f"weather: {weather_ctx}")
        colors = ", ".join(style_prefs.get("colors", []))
        styles = ", ".join(style_prefs.get("styles", []))
        if colors:
            parts.append(f"colors: {colors}")
        if styles:
            parts.append(f"style: {styles}")

        composite = ". ".join(p for p in parts if p)
        logger.debug("FashionDomain.encode_query composite=%r", composite[:120])
        return encode_text(composite)

    def encode_item(self, item: Item) -> np.ndarray:
        """
        Encode an item into a 512-dim vector.

        Returns the cached embedding if the item already has one (fast path).
        Otherwise builds a text description from the item's attributes and encodes it.
        This is the slow path — only reached for items that haven't been backfilled yet.

        Args:
            item: Item dataclass. item.embedding is used directly if non-None.

        Returns:
            512-dim float32 unit vector.
        """
        if item.embedding is not None:
            return item.embedding

        # Build a descriptive text from available attributes
        parts = [item.title]
        attrs = item.attributes
        if attrs.get("brand"):
            parts.append(attrs["brand"])
        if attrs.get("category"):
            parts.append(attrs["category"])
        if attrs.get("colors"):
            parts.append(", ".join(attrs["colors"]))
        if attrs.get("condition"):
            parts.append(attrs["condition"])

        return encode_text(" ".join(parts))

    def parse_item(self, raw: dict) -> Item:
        """
        Convert a catalog_items DB row dict into an Item dataclass.

        Handles the embedding column: psycopg3 returns it as a Python list
        (when pgvector's register_vector is not called) or an np.ndarray
        (when it is). We normalize to np.ndarray either way.

        Args:
            raw: Dict with keys matching catalog_items columns.

        Returns:
            Populated Item dataclass.
        """
        embedding_raw = raw.get("embedding")
        embedding = None
        if embedding_raw is not None:
            embedding = (
                embedding_raw
                if isinstance(embedding_raw, np.ndarray)
                else np.array(embedding_raw, dtype=np.float32)
            )

        return Item(
            item_id=raw["item_id"],
            domain=raw.get("domain", "fashion"),
            title=raw.get("title") or "",
            price=float(raw.get("price") or 0.0),
            image_url=raw.get("image_url") or "",
            product_url=raw.get("product_url") or "",
            source=raw.get("source") or "",
            embedding=embedding,
            attributes=raw.get("attributes") or {},
        )

    def preference_context(self, query: dict, item: Item) -> dict:
        """
        Stub for the Week 8 Bradley-Terry reranker.

        Args:
            query: Structured query dict passed to the recommendation pipeline.
            item: Candidate item being considered.

        Returns:
            Minimal context dict for the future reranker.
        """
        return {"query": query, "item_id": item.item_id, "domain": "fashion"}
