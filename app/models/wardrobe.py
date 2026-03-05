from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict

CategoryType = Literal["tops", "bottoms", "outerwear", "shoes", "accessories"]


class WardrobeItemCreate(BaseModel):
    """Request body for creating a new wardrobe item (metadata only; image is multipart)."""

    name: str
    category: Optional[CategoryType] = None


class WardrobeItemUpdate(BaseModel):
    """Request body for updating wardrobe item metadata (all fields optional)."""

    name: Optional[str] = None
    category: Optional[CategoryType] = None
    tags: Optional[list[str]] = None


class WardrobeItemResponse(BaseModel):
    """JSON-serialisable representation of a wardrobe item returned by the API."""

    item_id: str
    name: str
    category: Optional[str]
    image_url: Optional[
        str
    ]  # presigned S3 GET URL (1 h expiry); None when no image uploaded
    tags: list[str]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
