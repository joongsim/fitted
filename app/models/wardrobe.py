from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class WardrobeItemCreate(BaseModel):
    """Request body for creating a new wardrobe item (metadata only; image is multipart)."""

    name: str
    category: Optional[str] = (
        None  # 'tops' | 'bottoms' | 'outerwear' | 'shoes' | 'accessories'
    )


class WardrobeItemUpdate(BaseModel):
    """Request body for updating wardrobe item metadata (all fields optional)."""

    name: Optional[str] = None
    category: Optional[str] = None
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
