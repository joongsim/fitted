from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

import numpy as np


@dataclass
class Item:
    item_id: str
    domain: str                         # 'fashion' | 'furniture' | ...
    title: str
    price: float
    image_url: str
    product_url: str
    source: str                         # 'poshmark_seed' | 'serpapi' | ...
    embedding: Optional[np.ndarray]     # 512-dim float32, L2-normalized; None until embedded
    attributes: dict = field(default_factory=dict)  # brand, size, condition, colors, ...