"""Pydantic models for catalog items and Poshmark API responses."""

import hashlib
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class PriceAmount(BaseModel):
    """Poshmark price representation (returned as string)."""

    val: str


class CoverShot(BaseModel):
    """Poshmark cover image URLs."""

    url_small: Optional[str] = None
    url_large: Optional[str] = None


class PoshmarkListingRaw(BaseModel):
    """
    Raw Poshmark listing as returned by the RapidAPI endpoint.

    Uses extra="allow" to tolerate undocumented fields from the API.
    Only whitelisted fields are written to the database — see to_attributes().
    """

    id: str
    title: Optional[str] = None
    description: Optional[str] = None
    price_amount: Optional[PriceAmount] = None
    original_price_amount: Optional[PriceAmount] = None
    condition: Optional[str] = None  # nwt | nwot | good | fair | poor
    brand: Optional[str] = None
    size: Optional[str] = None
    colors: Optional[List[str]] = Field(default_factory=list)
    category: Optional[str] = None
    department: Optional[str] = None  # Women | Men | Kids
    cover_shot: Optional[CoverShot] = None
    seller: Optional[dict] = None

    model_config = ConfigDict(extra="allow")

    def to_attributes(self) -> dict:
        """
        Build the attributes JSONB dict from whitelisted fields only.

        Populates only known string/scalar fields to prevent unvalidated
        API data from bulk-propagating into the database.
        """
        attrs: dict = {}

        if self.brand is not None:
            attrs["brand"] = str(self.brand)[:255]
        if self.size is not None:
            attrs["size"] = str(self.size)[:50]
        if self.condition is not None:
            attrs["condition"] = str(self.condition)[:50]
        if self.colors:
            attrs["colors"] = [str(c)[:50] for c in self.colors[:10]]
        if self.category is not None:
            attrs["category"] = str(self.category)[:100]
        if self.department is not None:
            attrs["department"] = str(self.department)[:50]
        if self.description is not None:
            attrs["description"] = str(self.description)[:2000]
        if self.original_price_amount is not None:
            try:
                attrs["original_price"] = float(self.original_price_amount.val)
            except (ValueError, TypeError):
                pass
        if self.cover_shot is not None and self.cover_shot.url_small is not None:
            attrs["cover_shot_small"] = str(self.cover_shot.url_small)[:500]
        if self.seller is not None:
            username = self.seller.get("username")
            rating = self.seller.get("seller_rating")
            if username and isinstance(username, str):
                attrs["seller_username"] = username[:100]
            if rating is not None:
                try:
                    attrs["seller_rating"] = float(rating)
                except (ValueError, TypeError):
                    pass
        attrs["is_available"] = True
        return attrs


class CatalogItemCreate(BaseModel):
    """Normalized catalog item ready for DB insertion."""

    item_id: str
    domain: str = "fashion"
    title: Optional[str] = None
    price: Optional[float] = None
    image_url: Optional[str] = None  # S3 URL after image copy
    product_url: Optional[str] = None
    source: str = "poshmark_seed"
    content_hash: Optional[str] = None  # SHA-256 of title:price:brand:category
    attributes: dict = Field(default_factory=dict)
    # embedding omitted — NULL until CLIP service is built


class CatalogItemDB(CatalogItemCreate):
    """Catalog item as returned from the database (includes timestamps)."""

    first_seen: datetime
    last_seen: datetime
    hit_count: int = 1
    model_version: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


def make_content_hash(title: str, price: float, brand: str, category: str) -> str:
    """
    Compute a SHA-256 content hash for deduplication.

    The hash is deterministic for a given (title, price, brand, category)
    combination and is used to detect likely duplicate listings.
    """
    raw = f"{title}:{price}:{brand}:{category}".lower()
    return hashlib.sha256(raw.encode()).hexdigest()
