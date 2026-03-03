from typing import Optional
from pydantic import BaseModel


class ProductRecommendation(BaseModel):
    item_id: str
    title: str
    price: float
    product_url: str
    image_url: Optional[str] = None
    similarity_score: float           # cosine similarity after two-tower projection; range [-1, 1]
    attributes: dict = {}             # brand, size, category, colors — from catalog_items.attributes
    llm_explanation: Optional[str] = None  # only populated when include_explanation=True