"""Domain protocol — domain-agnostic interface for the recommendation pipeline."""

from typing import Protocol, runtime_checkable

import numpy as np

from app.models.item import Item


@runtime_checkable
class Domain(Protocol):
    """
    A domain encapsulates how to encode queries and items for a specific vertical.
    The recommendation pipeline only depends on this interface.
    """

    def encode_query(self, inputs: dict) -> np.ndarray:
        """
        Convert structured query inputs (weather + preferences + search text)
        into a 512-dim embedding vector.
        Expected keys in inputs: query_text, weather_context, style_preferences.
        """
        ...

    def encode_item(self, item: Item) -> np.ndarray:
        """
        Encode an Item into a 512-dim embedding vector.
        May return item.embedding directly if it's already populated.
        """
        ...

    def parse_item(self, raw: dict) -> Item:
        """Convert a raw DB row dict into an Item dataclass."""
        ...

    def preference_context(self, query: dict, item: Item) -> dict:
        """
        Build the context dict for the preference reranker (Week 8).
        Stub for now — return a minimal dict.
        """
        ...
