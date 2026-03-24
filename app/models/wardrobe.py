from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict

CategoryType = Literal["tops", "bottoms", "outerwear", "shoes", "accessories"]
EmbeddingStatusType = Literal["pending", "embedding", "done", "failed"]


class WardrobeItemCreate(BaseModel):
    name: str
    category: Optional[CategoryType] = None


class WardrobeItemUpdate(BaseModel):
    name: Optional[str] = None
    category: Optional[CategoryType] = None
    tags: Optional[list[str]] = None


class WardrobeItemResponse(BaseModel):
    item_id: str
    name: str
    category: Optional[str]
    image_url: Optional[str]
    tags: list[str]
    created_at: datetime
    embedding_status: EmbeddingStatusType = "pending"

    model_config = ConfigDict(from_attributes=True)


class WardrobeItemStatusResponse(BaseModel):
    item_id: str
    embedding_status: EmbeddingStatusType
