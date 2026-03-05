# Building a Two-Tower Recommendation System

This document walks through implementing the product recommendation pipeline for Fitted end-to-end. It is written as a tutorial: each section explains _why_ a piece exists before showing the code for it.

The full pipeline looks like this:

```
User request
  → LLM generates a natural-language search query
  → CLIP text encoder embeds the query (512-dim vector)
  → Vector cache checks for a semantically similar prior query
      ├── HIT  → return cached candidate list
      └── MISS → ANN search on catalog_items (Poshmark seed data)
                 → store results in cache
  → Build user embedding
      ├── wardrobe items: mean-pool CLIP image embeddings (highest fidelity)
      ├── style preference tags: mean-pool CLIP text embeddings (cold-start fallback)
      └── generic fallback: "casual everyday clothing" (no wardrobe + no tags)
  → Two-Tower ranking: project user + item vectors, score by cosine similarity
  → Bradley-Terry preference reranking: blend with pairwise preference scores (no-op at cold start)
  → [Optional] LLM explanation for top-K picks
  → Return top-K as ProductRecommendation objects
```

---

## Table of Contents

1. [Background: What Is a Two-Tower Model?](#1-background)
2. [The Item Representation: `Item` dataclass](#2-the-item-dataclass)
3. [The API Response Model: `ProductRecommendation`](#3-the-api-response-model)
4. [Embedding Text with CLIP](#4-embedding-text-with-clip)
5. [The Domain Protocol](#5-the-domain-protocol)
6. [FashionDomain: Encoding Queries and Items](#6-fashiondomain)
7. [The Vector Cache](#7-the-vector-cache)
8. [The Dev Catalog Service](#8-the-dev-catalog-service)
9. [Adding Search Query Generation to the LLM Service](#9-llm-additions)
10. [The Two-Tower Model](#10-the-two-tower-model)
11. [The Recommendation Service: Wiring It All Together](#11-the-recommendation-service)
12. [API Endpoints](#12-api-endpoints)
13. [The Catalog Embedding Backfill Script](#13-backfill-script)
14. [Testing Strategy](#14-testing-strategy)
15. [Late Interaction Reranking](#15-late-interaction-reranking)
16. [Future Architecture Directions](#16-future-architecture-directions)
17. [Wardrobe CRUD Service](#17-wardrobe-crud-service)
18. [Interaction Logging](#18-interaction-logging)
19. [The Bradley-Terry Preference Reranker](#19-the-bradley-terry-preference-reranker)
20. [Frontend: Wardrobe, Preferences, and Product Cards](#20-frontend-wardrobe-preferences-and-product-cards)
21. [Image Embedding: Wardrobe Photo Encoding](#21-image-embedding-wardrobe-photo-encoding)

---

## 1. Background

### What is a two-tower model?

A two-tower model is a retrieval architecture that learns separate vector representations for users and items, then ranks items by how close they are to the user in that shared embedding space.

```
User features ──► UserTower ──► user vector ──┐
                                               ├──► cosine similarity ──► ranking score
Item features ──► ItemTower ──► item vector ──┘
```

The "towers" are neural networks (or simpler projections) that map high-dimensional inputs into a shared lower-dimensional space. Once both sides are projected, ranking is a single dot product per candidate — cheap at inference time even with thousands of candidates.

### Why this architecture for Fitted?

Fitted needs to answer: _"Given this user's taste and today's weather, which Poshmark listings are most relevant?"_

The user and item are heterogeneous (different input types, different sizes), so a two-tower model lets us:
- Encode the user from wardrobe embeddings + style preferences
- Encode items from their CLIP image/text embeddings
- Compare them in a unified 512-dim space

### Cold-start vs trained weights

On day one there's no interaction data to train on. We handle this with **Xavier initialization**: the tower weights start as random matrices drawn from a uniform distribution scaled to keep activations from vanishing or exploding. With Xavier init, cosine similarity is still meaningful because both towers apply the same random projection — so relative distances are preserved.

When the training pipeline (Week 8) runs, it overwrites those weights with learned ones. The rest of the pipeline is identical either way.

> **Important — cold-start ranking quality:** With Xavier-initialized weights and no training data, two-tower ranking scores cluster near zero and are nearly indistinguishable across candidates. The ranking order before Week 8 is effectively random. This is expected and acceptable: the *retrieval* step (pgvector ANN search on CLIP embeddings) is semantically meaningful and returns plausible candidates; only the *reranking* step is uninformative until learned weights are loaded. Do not tune or A/B test ranking quality using the Xavier-initialized system — wait for the first trained checkpoint.

### What's complete

- **Two-tower ranking** — `UserTower` + `ItemTower` (Xavier init; S3 weight loading); full `recommend()` pipeline.
- **Bradley-Terry preference reranker** — `get_preference_scores` + `rerank`; no-op at cold start; feeds from `preference_pairs` table.
- **Interaction logging** — `POST /interactions` (click/save/dismiss) feeding future training data.
- **Wardrobe CRUD + frontend** — upload, gallery, delete; HTMX throughout.
- **Auth endpoints** — register, login, logout; JWT cookies.
- **Product cards** — `product_card()` in frontend with save/dismiss fire-and-forget to `/log-interaction`.

### What's next

- **CLIP image encoder** — `encode_image(url_or_s3_key)` in `embedding_service.py`; runs on EC2 sidecar (Lambda 250MB limit). Triggered after wardrobe photo upload to populate `wardrobe_items.embedding`. Until this is live, the user tower always falls back to cold-start style tag encoding.
- **Frontend recommendation flow** — wire `POST /recommend-products` into a "Shop" page or home-page section; render product card grid from API response.
- **Training loop** — `scripts/train_two_towers.py` with `TripletMarginLoss`. Comes in Week 8 once enough interaction data accumulates.

---

## 2. The Item Dataclass

**File:** `app/models/item.py`

Everything in the pipeline passes items around as this dataclass. We use a dataclass rather than a Pydantic model because numpy arrays don't serialize through Pydantic cleanly (you'd need custom validators), and this type is internal — it never goes over the wire directly.

```python
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
    source: str                         # 'poshmark_seed' | ...
    embedding: Optional[np.ndarray]     # 512-dim float32, L2-normalized; None until embedded
    attributes: dict = field(default_factory=dict)  # brand, size, condition, colors, ...
```

The `embedding` field is `None` for items that haven't been embedded yet (most of the Poshmark catalog on day one). The downstream code handles this: when ranking, if an item has no embedding, we encode its title on the fly via CLIP text.

---

## 3. The API Response Model

**File:** `app/models/product.py`

This is what gets serialized to JSON and returned to the client. It's a Pydantic model because it _does_ go over the wire.

```python
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
```

`similarity_score` is exposed to clients so the frontend can show a confidence indicator or filter below a threshold. `llm_explanation` is optional because calling the LLM adds ~1–2s of latency — we only do it when the client explicitly asks.

---

## 4. Embedding Text with CLIP

**File:** `app/services/embedding_service.py`

CLIP (Contrastive Language-Image Pretraining) is a model trained to embed images and text into the same vector space. A photo of a "red leather jacket" and the text "red leather jacket" end up close together in CLIP's 512-dimensional space. This makes it ideal for fashion recommendations: you can embed a query like "warm casual streetwear" and find items near it even if none of the titles contain those exact words.

We use only the **text encoder** for now. The full ViT-B/32 model is ~340MB; Lambda's zip package limit is 250MB. The text-only path is much lighter and still gives us the shared embedding space. Image encoding is added in a later phase via two separate paths:

- **Catalog backfill** (offline, batch): runs locally on an RTX 3080. Model size is not a Lambda constraint here — the script runs once, outside of the serving path.
- **User wardrobe photo uploads** (online, real-time): runs on an EC2 sidecar. A user uploading a new jacket photo needs the embedding computed immediately; the local machine is not an option for a production serving path.

### The lazy-import pattern

`torch` and `open_clip` are heavy. Importing them at module load time would add several seconds to every Lambda cold start — even for requests that never touch the embedding service. We use a module-level singleton with a lazy initializer:

```python
import logging
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

# Module-level singletons — None until first call
_model = None
_tokenizer = None
_CLIP_MODEL = "ViT-B-32"
_CLIP_PRETRAINED = "laion400m_e32"


def _load_model():
    """Load CLIP once; cache for the lifetime of the warm Lambda instance."""
    global _model, _tokenizer
    if _model is not None:
        return _model, _tokenizer

    import open_clip  # lazy import — torch is not loaded until this line
    import torch

    logger.info("Loading CLIP model %s (first call)", _CLIP_MODEL)
    model, _, _ = open_clip.create_model_and_transforms(
        _CLIP_MODEL, pretrained=_CLIP_PRETRAINED
    )
    model.eval()
    tokenizer = open_clip.get_tokenizer(_CLIP_MODEL)
    _model, _tokenizer = model, tokenizer
    return _model, _tokenizer


def encode_text(text: str) -> np.ndarray:
    """
    Encode a text string using the CLIP ViT-B/32 text encoder.

    Returns a 512-dim float32 numpy array, L2-normalized (unit vector).
    L2 normalization means cosine similarity == dot product, which is faster
    to compute at ranking time.

    Args:
        text: Input string. Automatically truncated to CLIP's 77-token limit.

    Returns:
        np.ndarray of shape (512,), dtype float32, unit norm.
    """
    import torch

    model, tokenizer = _load_model()

    tokens = tokenizer([text])
    with torch.no_grad():
        features = model.encode_text(tokens)
        features = features / features.norm(dim=-1, keepdim=True)  # L2 normalize

    embedding: np.ndarray = features.cpu().numpy().astype(np.float32)[0]
    logger.debug(
        "encode_text: text=%r shape=%s norm=%.4f",
        text[:80],
        embedding.shape,
        float(np.linalg.norm(embedding)),
    )
    return embedding


def reset_model_for_testing() -> None:
    """Reset cached singletons — call in test teardown to avoid state leakage."""
    global _model, _tokenizer
    _model = None
    _tokenizer = None
```

### Why L2 normalization?

After encoding, we divide the vector by its magnitude so it becomes a unit vector (norm = 1). Then:

```
cosine_similarity(a, b) = a · b / (|a| × |b|)
                        = a · b / (1 × 1)
                        = a · b
```

Dot products are significantly faster than full cosine similarity on large candidate sets. pgvector's `<=>` operator (cosine distance) does the same thing internally, but having normalized vectors in Python means we can also rank with `np.dot` without calling the database for every comparison.

### The shared image-text space

CLIP was trained on 400 million image-text pairs using a contrastive objective: matched pairs are pulled together in embedding space, unmatched pairs are pushed apart. The result is a 512-dimensional space where images and text coexist — a photo of a grey cashmere sweater and the string `"grey cashmere sweater"` land near each other even though they are completely different data types.

This property is what makes CLIP well-suited for fashion recommendation:

- A text query ("warm casual streetwear") retrieves visually similar items even if their titles don't contain those exact words
- User style preferences expressed as text ("ivy prep", "streetwear") can be compared directly against item embeddings
- Image and text signals from the same user can be averaged into a single meaningful vector

This joint image-text embedding space is the architecture behind Pinterest Lens, Google Shopping's visual search, and Shopify's product search. We currently use only CLIP's text encoder — the image encoder is ~340MB, which exceeds Lambda's 250MB zip limit. Image encoding is handled in a later phase: catalog item images are embedded in bulk via a local backfill (RTX 3080), while user-uploaded wardrobe photos are embedded in real time via an EC2 sidecar. When both paths are active, item embeddings shift from text-derived to image-derived vectors with no changes to the retrieval or ranking pipeline — both modalities live in the same 512-dim space.

### CLIP vs. SigLIP

SigLIP (Sigmoid Loss for Language-Image Pre-training, Google 2023) is a drop-in alternative to CLIP that changes only the training objective. CLIP uses InfoNCE loss — a softmax over the full batch that treats every other pair in the batch as a negative example, requiring large batches (4096+) to work well. SigLIP uses a sigmoid loss that treats each (image, text) pair as an independent binary classification: matched or not. This makes it less sensitive to batch size and produces embeddings with better precision for fine-grained queries.

For fashion recommendation, SigLIP's advantages are meaningful:

- Style labels like `"ivy prep"` and `"smart casual"` embed more precisely relative to item images — the sigmoid objective penalises ambiguous near-matches more aggressively than InfoNCE
- Zero-shot classification accuracy on fine-grained visual categories is consistently higher, which matters when the catalog includes subtle style distinctions
- SigLIP2 (2024) adds strong multilingual support, useful if the product expands internationally

The trade-off is ecosystem maturity. `open_clip` has extensive CLIP support and a straightforward API; SigLIP support is newer and less battle-tested in production pipelines. The embedding space is also less widely characterised — there is less published work on what SigLIP's space looks like for fashion specifically. CLIP is the pragmatic default; SigLIP is worth benchmarking once the catalog is large enough to measure retrieval quality differences meaningfully.

### Item representation: feature extraction vs. direct embedding

An alternative to encoding item titles and attributes directly would be to first extract structured features — `'streetwear'`, `'grey'`, `'cashmere'` — and embed those instead. The appeal is normalisation: Poshmark listing titles are noisy (`"LIKE NEW 🔥 Alexander Wang grey cashmere crewneck size M"`), and two listings for the same type of item may produce more different CLIP vectors than they should.

We do not do this for three reasons:

1. **`attributes` already covers the structured part.** `encode_item` uses `colors`, `brand`, `category`, and `condition` from the structured attributes column, not just the raw title. Most listing-title noise is already bypassed.
2. **CLIP is robust to natural language noise.** It was trained on raw internet text — inconsistent, emoji-laden, promotional. The model handles the conventions of secondhand listing titles well.
3. **Feature extraction loses information.** Compressing a listing to a tag list discards signals that CLIP captures implicitly: exact colorway, specific brand associations learned during training, design details that don't have obvious label names.

Feature extraction would be worth revisiting if the catalog data were significantly noisier than the `attributes` dict, or if interpretability of item representations became a product requirement — for example, surfacing which style tags drove a recommendation to the user.

---

## 5. The Domain Protocol

**File:** `app/services/domain.py`

The recommendation pipeline is designed to be domain-agnostic. "Fashion" is the first vertical, but the same code could rank furniture, electronics, or any other product catalog by swapping out the domain.

We enforce this with a Python `Protocol` — structural subtyping that doesn't require inheritance:

```python
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
```

Note that `fetch_candidates` is **not** part of the protocol. Candidate sourcing (which database table? which API?) is handled by a separate `CandidateSource` abstraction injected at the service level. The domain only knows how to encode; where items come from is separate.

### The factory

**File:** `app/services/domain_factory.py`

```python
import logging
import os

from app.services.domain import Domain
from app.services.domains.fashion import FashionDomain

logger = logging.getLogger(__name__)

_REGISTRY: dict[str, type] = {
    "fashion": FashionDomain,
}


def get_domain(name: str | None = None) -> Domain:
    """
    Return a Domain instance by name.
    Reads the DOMAIN environment variable if name is None; defaults to 'fashion'.

    Raises ValueError for unregistered domain names.
    """
    domain_name = name or os.environ.get("DOMAIN", "fashion")
    cls = _REGISTRY.get(domain_name)
    if cls is None:
        raise ValueError(
            f"Unknown domain '{domain_name}'. Available: {list(_REGISTRY.keys())}"
        )
    logger.debug("Domain resolved: %s -> %s", domain_name, cls.__name__)
    return cls()
```

Adding a new vertical later is a one-liner in `_REGISTRY` plus a new file in `app/services/domains/`.

---

## 6. FashionDomain

**File:** `app/services/domains/fashion.py`

The concrete implementation of the `Domain` protocol for fashion products.

```python
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
        """Stub for the Week 8 Bradley-Terry reranker."""
        return {"query": query, "item_id": item.item_id, "domain": "fashion"}
```

---

## 7. The Vector Cache

**File:** `app/services/vector_cache.py`

Every recommendation request starts with embedding a query. If two requests are semantically similar — "casual summer outfit" vs "light summer clothing" — we shouldn't hit the catalog twice. The vector cache stores prior query embeddings in `query_cache` and looks up the nearest one using pgvector's cosine distance.

### Schema reminder

```sql
CREATE TABLE query_cache (
    cache_id        UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    query_hash      VARCHAR UNIQUE NOT NULL,  -- SHA-256 for exact dedup
    query_text      TEXT,
    query_embedding VECTOR(512),
    s3_key          TEXT,                     -- pointer to serialized Item list in S3
    expires_at      TIMESTAMPTZ
);
CREATE INDEX ON query_cache USING hnsw (query_embedding vector_cosine_ops);
```

The candidate list is stored in S3 (not inline in the table) to avoid bloating the HNSW index with binary blob data. A cache hit costs one extra S3 `GetObject` (~5ms), which is well worth avoiding a full catalog ANN search.

### Adding query_cache to db_migrate.py

**`query_cache` is not created by the existing migration script.** Add the following block inside `scripts/db_migrate.py`, alongside the existing table creation statements:

```python
# In scripts/db_migrate.py — add inside run_migrations() after the catalog_items block

cur.execute("""
    CREATE TABLE IF NOT EXISTS query_cache (
        cache_id        UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        query_hash      VARCHAR UNIQUE NOT NULL,
        query_text      TEXT,
        query_embedding VECTOR(512),
        s3_key          TEXT,
        expires_at      TIMESTAMPTZ
    )
""")
cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_query_cache_embedding
    ON query_cache USING hnsw (query_embedding vector_cosine_ops)
""")
cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_query_cache_expires
    ON query_cache (expires_at)
""")
logger.info("query_cache table and indexes created")
```

The secondary index on `expires_at` makes the `WHERE expires_at > NOW()` filter in the lookup query a range scan rather than a full table scan, which matters once the cache grows to thousands of entries.

Run the migration before deploying the recommendation service for the first time:

```bash
PYTHONPATH=. python scripts/db_migrate.py
```

### Lookup

```python
import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

import numpy as np

from app.models.item import Item
from app.services.db_service import get_connection

logger = logging.getLogger(__name__)

_CACHE_TTL_HOURS = 24
_COSINE_THRESHOLD = 0.15  # cosine *distance* threshold; 0 = identical, 2 = opposite
                           # distance = 1 - similarity, so 0.15 ≈ similarity 0.85


async def lookup(
    query_embedding: np.ndarray,
    threshold: float = _COSINE_THRESHOLD,
) -> Optional[tuple[list[Item], str]]:
    """
    Search query_cache for a semantically similar prior query.

    pgvector's <=> operator returns cosine *distance* (not similarity).
    We look for entries where that distance is below our threshold —
    meaning the cached query was similar enough that its results are reusable.

    Args:
        query_embedding: 512-dim L2-normalized query vector.
        threshold: Cosine distance threshold. Lower = stricter.

    Returns:
        (items, cache_id) on HIT, or None on MISS.
    """
    embedding_list = query_embedding.tolist()

    async with get_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT cache_id, s3_key,
                       (query_embedding <=> %s::vector) AS cosine_distance
                FROM query_cache
                WHERE expires_at > NOW()
                  AND (query_embedding <=> %s::vector) < %s
                ORDER BY cosine_distance
                LIMIT 1
                """,
                (embedding_list, embedding_list, threshold),
            )
            row = await cur.fetchone()

    if not row:
        logger.debug("Vector cache MISS (no entry within threshold=%.3f)", threshold)
        return None

    cache_id, s3_key, distance = row
    logger.info(
        "Vector cache HIT: cache_id=%s cosine_distance=%.4f", cache_id, distance
    )

    items = await _load_items_from_s3(s3_key)
    if items is None:
        logger.warning("Cache HIT but S3 load failed for %s — treating as MISS", s3_key)
        return None

    return items, str(cache_id)
```

### Store

```python
async def store(
    query_text: str,
    query_embedding: np.ndarray,
    items: list[Item],
    s3_client,
    bucket: str,
    ttl_hours: int = _CACHE_TTL_HOURS,
) -> Optional[str]:
    """
    Serialize items to S3 and upsert a query_cache row.

    Uses ON CONFLICT (query_hash) DO UPDATE so that re-running the same query
    refreshes the TTL instead of inserting a duplicate.
    """
    cache_id = str(uuid.uuid4())
    s3_key = f"cache/query/{cache_id}.json"
    expires_at = datetime.now(tz=timezone.utc) + timedelta(hours=ttl_hours)
    q_hash = hashlib.sha256(query_text.encode()).hexdigest()
    embedding_list = query_embedding.tolist()

    # Write candidate list to S3 first — if DB upsert fails, S3 is orphaned but harmless
    try:
        payload = json.dumps([_item_to_dict(item) for item in items])
        s3_client.put_object(
            Bucket=bucket,
            Key=s3_key,
            Body=payload.encode("utf-8"),
            ContentType="application/json",
        )
    except Exception:
        logger.error("Failed to write cache to S3 key=%s", s3_key, exc_info=True)
        return None

    try:
        async with get_connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO query_cache
                        (cache_id, query_hash, query_text, query_embedding, s3_key, expires_at)
                    VALUES (%s, %s, %s, %s::vector, %s, %s)
                    ON CONFLICT (query_hash) DO UPDATE SET
                        query_embedding = EXCLUDED.query_embedding,
                        s3_key          = EXCLUDED.s3_key,
                        expires_at      = EXCLUDED.expires_at
                    """,
                    (cache_id, q_hash, query_text, embedding_list, s3_key, expires_at),
                )
                await conn.commit()
    except Exception:
        logger.error("Failed to upsert query_cache row", exc_info=True)
        return None

    logger.info("Vector cache STORED: cache_id=%s expires=%s", cache_id, expires_at)
    return cache_id
```

### Serialization helpers

The `Item` dataclass contains a numpy array which isn't JSON-serializable by default. We convert to/from plain dicts:

```python
def _item_to_dict(item: Item) -> dict:
    return {
        "item_id": item.item_id,
        "domain": item.domain,
        "title": item.title,
        "price": item.price,
        "image_url": item.image_url,
        "product_url": item.product_url,
        "source": item.source,
        "embedding": item.embedding.tolist() if item.embedding is not None else None,
        "attributes": item.attributes,
    }


def _dict_to_item(d: dict) -> Item:
    embedding_raw = d.get("embedding")
    embedding = np.array(embedding_raw, dtype=np.float32) if embedding_raw else None
    return Item(
        item_id=d["item_id"],
        domain=d["domain"],
        title=d["title"],
        price=float(d["price"]),
        image_url=d.get("image_url") or "",
        product_url=d.get("product_url") or "",
        source=d.get("source") or "",
        embedding=embedding,
        attributes=d.get("attributes") or {},
    )


async def _load_items_from_s3(s3_key: str) -> Optional[list[Item]]:
    import boto3
    from app.core.config import config

    try:
        s3 = boto3.client("s3")
        response = s3.get_object(Bucket=config.weather_bucket_name, Key=s3_key)
        raw = json.loads(response["Body"].read().decode("utf-8"))
        return [_dict_to_item(d) for d in raw]
    except Exception:
        logger.error("Failed to load items from S3 key=%s", s3_key, exc_info=True)
        return None
```

---

## 8. The Dev Catalog Service

**File:** `app/services/dev_catalog_service.py`

This service retrieves candidates from the `catalog_items` table — populated by the Poshmark ingestion script. It is the production candidate source; no live search integration is planned.

```python
import logging

import numpy as np

from app.models.item import Item
from app.services.db_service import get_connection

logger = logging.getLogger(__name__)


async def search(
    query_embedding: np.ndarray,
    limit: int = 50,
    domain: str = "fashion",
) -> list[Item]:
    """
    Retrieve candidate Items from catalog_items via pgvector ANN search.

    Primary path: ORDER BY (embedding <=> query_embedding) — cosine distance,
    ascending (nearest first). Only rows where embedding IS NOT NULL are eligible.

    Fallback path: When no embedded rows exist (e.g., before the backfill script
    has run), fall back to returning the most recently seen items. This ensures
    the recommendation pipeline returns _something_ even on a fresh DB.

    Args:
        query_embedding: 512-dim L2-normalized query vector.
        limit: Maximum candidates to return.
        domain: Domain filter ('fashion', 'furniture', ...).

    Returns:
        List of Item objects. Order is by cosine similarity when embeddings
        exist, by recency otherwise.
    """
    embedding_list = query_embedding.tolist()

    async with get_connection() as conn:
        async with conn.cursor() as cur:
            # Primary: vector search on rows that have been embedded
            await cur.execute(
                """
                SELECT item_id, domain, title, price, image_url, product_url,
                       source, embedding, attributes,
                       (embedding <=> %s::vector) AS cosine_distance
                FROM catalog_items
                WHERE domain = %s
                  AND embedding IS NOT NULL
                ORDER BY cosine_distance
                LIMIT %s
                """,
                (embedding_list, domain, limit),
            )
            rows = await cur.fetchall()

            if not rows:
                logger.warning(
                    "No embedded catalog items for domain=%s — falling back to recency",
                    domain,
                )
                await cur.execute(
                    """
                    SELECT item_id, domain, title, price, image_url, product_url,
                           source, embedding, attributes, NULL AS cosine_distance
                    FROM catalog_items
                    WHERE domain = %s
                    ORDER BY last_seen DESC
                    LIMIT %s
                    """,
                    (domain, limit),
                )
                rows = await cur.fetchall()

    items = []
    for row in rows:
        item_id, dom, title, price, image_url, product_url, source, emb_raw, attrs, _ = row
        embedding = (
            np.array(emb_raw, dtype=np.float32) if emb_raw is not None else None
        )
        items.append(
            Item(
                item_id=str(item_id),
                domain=dom or domain,
                title=title or "",
                price=float(price or 0.0),
                image_url=image_url or "",
                product_url=product_url or "",
                source=source or "",
                embedding=embedding,
                attributes=attrs or {},
            )
        )

    logger.info(
        "dev_catalog_service.search: domain=%s limit=%d -> %d candidates",
        domain,
        limit,
        len(items),
    )
    return items
```

---

## 9. LLM Additions

**File:** `app/services/llm_service.py` (additions)

We add two functions to the existing LLM service. These follow the same pattern as `get_outfit_suggestion` — try the LLM, fall back gracefully on failure.

### `generate_search_query`

The LLM knows about fashion and weather. Given the user's preferences and current conditions, it generates a search query that's better than anything we could construct with simple rules.

```python
async def generate_search_query(
    preferences: dict,
    weather: dict,
) -> str:
    """
    Generate a product search query string from user preferences + weather context.

    The returned string is short (5–10 words) and suitable for embedding and
    passing to the catalog search. It's NOT a SQL query or a structured filter —
    it's a natural-language description of what the user might want to wear.

    Args:
        preferences: User's style_preferences dict (colors, styles, occasions, avoid).
        weather: Dict with temp_c, condition, and optionally location.

    Returns:
        A short query string, e.g. "navy chinos casual warm weather".
        Falls back to a template string if the LLM call fails.
    """
    condition = weather.get("condition", "")
    temp_c = weather.get("temp_c", 20)
    styles = ", ".join(preferences.get("styles", ["casual"]))
    colors = ", ".join(preferences.get("colors", []))
    avoid = ", ".join(preferences.get("avoid", []))

    prompt = (
        f"Weather: {condition}, {temp_c}°C. "
        f"User style: {styles}. "
        + (f"Preferred colors: {colors}. " if colors else "")
        + (f"Avoid: {avoid}. " if avoid else "")
        + "Write a 5–10 word clothing search query for this person. "
        "Only output the query, no explanation."
    )

    try:
        client = get_client()
        if not client:
            return _fallback_search_query(preferences, weather)

        logger.info(
            "Generating search query via LLM: temp_c=%.1f condition=%s",
            temp_c,
            condition,
        )
        response = await client.chat.completions.create(
            model=_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=50,
        )
        query = response.choices[0].message.content.strip().strip('"').strip("'")
        logger.info("Generated search query: %r", query)
        return query

    except Exception:
        logger.error("LLM search query generation failed — using fallback", exc_info=True)
        return _fallback_search_query(preferences, weather)


def _fallback_search_query(preferences: dict, weather: dict) -> str:
    """Rule-based fallback when LLM is unavailable."""
    style = preferences.get("styles", ["casual"])[0] if preferences.get("styles") else "casual"
    condition = weather.get("condition", "").lower()
    temp_c = weather.get("temp_c", 20)

    if temp_c < 10:
        weather_desc = "cold weather"
    elif temp_c < 20:
        weather_desc = "cool weather"
    else:
        weather_desc = "warm weather"

    return f"{style} men clothing {weather_desc}"
```

### `generate_explanation`

When the client requests it, we can narrate why the top items were recommended. This is a "nice to have" — it adds personality but is gated behind `include_explanation=True`.

```python
async def generate_explanation(
    top_items: list[dict],
    weather_context: dict,
    style_preferences: dict,
) -> str:
    """
    Generate a 2–3 sentence natural-language explanation for a set of recommendations.

    Args:
        top_items: List of dicts with keys title, price, attributes (top 3 is enough).
        weather_context: Dict with temp_c, condition.
        style_preferences: User's style_preferences dict.

    Returns:
        A short explanation string, or an empty string on failure.
    """
    items_summary = "; ".join(
        f"{item['title']} (${item['price']:.0f})" for item in top_items[:3]
    )
    condition = weather_context.get("condition", "")
    temp_c = weather_context.get("temp_c", 20)

    prompt = (
        f"Weather: {condition}, {temp_c}°C. "
        f"User preferences: {style_preferences}. "
        f"Top recommended items: {items_summary}. "
        "In 2–3 sentences, explain why these items suit this person today. "
        "Be concise and conversational."
    )

    try:
        client = get_client()
        if not client:
            return ""

        response = await client.chat.completions.create(
            model=_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.5,
            max_tokens=150,
        )
        return response.choices[0].message.content.strip()

    except Exception:
        logger.error("LLM explanation generation failed", exc_info=True)
        return ""
```

---

## 10. The Two-Tower Model

**File:** `app/services/recommendation_service.py`

This is the core of the recommendation system. Let's build it up piece by piece.

### Why numpy instead of torch for inference?

Each tower is a single matrix multiply: `output = W @ input`. Numpy handles this in one line and is fast enough for hundreds of candidates. Using torch would add startup overhead (initializing CUDA checks, loading torch ops) on every Lambda cold start. When the training loop runs on EC2 (Week 8), it will use torch — but it saves weights in a format we can load as numpy arrays.

### Xavier initialization

Xavier uniform init keeps the scale of activations consistent across layers:

```
scale = sqrt(6 / (fan_in + fan_out))
W ~ Uniform(-scale, scale)
```

For a 512×512 matrix: `scale = sqrt(6 / 1024) ≈ 0.077`. This means the initial dot products between projected vectors will be in a reasonable range — not vanishingly small, not explosively large.

```python
import io
import logging
from typing import Optional

import numpy as np

from app.models.item import Item
from app.models.product import ProductRecommendation
from app.services.db_service import get_connection

logger = logging.getLogger(__name__)

_S3_MODEL_KEY = "models/two-towers/latest.pt"
_EMBED_DIM = 512


class UserTower:
    """
    Linear projection from 512-dim user embedding to 512-dim ranking space.
    Initialized with Xavier uniform weights when no pre-trained model is available.
    """

    def __init__(self, weights: Optional[np.ndarray] = None) -> None:
        if weights is not None:
            self.W: np.ndarray = weights.astype(np.float32)
            logger.info("UserTower: loaded pre-trained weights, shape=%s", self.W.shape)
        else:
            scale = np.sqrt(6.0 / (_EMBED_DIM + _EMBED_DIM))
            self.W = np.random.uniform(
                -scale, scale, (_EMBED_DIM, _EMBED_DIM)
            ).astype(np.float32)
            logger.info("UserTower: Xavier-initialized (no pre-trained model found).")

    def forward(self, user_embedding: np.ndarray) -> np.ndarray:
        """
        Project user_embedding through the tower.

        Args:
            user_embedding: (512,) float32 input vector.

        Returns:
            (512,) L2-normalized float32 output vector.
        """
        projected = self.W @ user_embedding
        norm = np.linalg.norm(projected)
        return projected / norm if norm > 1e-8 else projected


class ItemTower:
    """
    Linear projection from 512-dim item embedding to 512-dim ranking space.
    Xavier-initialized when no pre-trained model is available.
    """

    def __init__(self, weights: Optional[np.ndarray] = None) -> None:
        if weights is not None:
            self.W: np.ndarray = weights.astype(np.float32)
            logger.info("ItemTower: loaded pre-trained weights, shape=%s", self.W.shape)
        else:
            scale = np.sqrt(6.0 / (_EMBED_DIM + _EMBED_DIM))
            self.W = np.random.uniform(
                -scale, scale, (_EMBED_DIM, _EMBED_DIM)
            ).astype(np.float32)
            logger.info("ItemTower: Xavier-initialized (no pre-trained model found).")

    def forward(self, item_embedding: np.ndarray) -> np.ndarray:
        """
        Project item_embedding through the tower.

        Args:
            item_embedding: (512,) float32 input vector.

        Returns:
            (512,) L2-normalized float32 output vector.
        """
        projected = self.W @ item_embedding
        norm = np.linalg.norm(projected)
        return projected / norm if norm > 1e-8 else projected
```

### Loading weights from S3

```python
def _load_towers_from_s3(s3_client, bucket: str) -> Optional[dict]:
    """
    Attempt to load two-tower weights from S3.

    The training script (Week 8) saves weights as:
        torch.save({'user_tower_W': tensor, 'item_tower_W': tensor}, path)

    torch.load returns tensors; we convert to numpy for inference.

    Returns:
        Dict with 'user_tower_W' and 'item_tower_W' as np.ndarray, or None if
        the model file doesn't exist yet (Xavier cold start).
    """
    try:
        import torch

        response = s3_client.get_object(Bucket=bucket, Key=_S3_MODEL_KEY)
        buffer = io.BytesIO(response["Body"].read())
        # weights_only=True is required to prevent arbitrary code execution.
        # torch.load deserializes a pickle stream; without this flag a
        # maliciously crafted .pt file in S3 can run arbitrary Python at
        # load time. weights_only restricts deserialization to tensors only.
        state = torch.load(buffer, map_location="cpu", weights_only=True)
        logger.info("Two-tower weights loaded from s3://%s/%s", bucket, _S3_MODEL_KEY)
        return {
            "user_tower_W": state["user_tower_W"].numpy(),
            "item_tower_W": state["item_tower_W"].numpy(),
        }
    except Exception:
        logger.info(
            "No weights at s3://%s/%s — using Xavier cold start.", bucket, _S3_MODEL_KEY
        )
        return None
```

---

## 11. The Recommendation Service

**File:** `app/services/recommendation_service.py` (continued)

`RecommendationService` is the orchestrator. It holds the tower instances and wires all the other services together.

### User signal sources

`_build_user_embedding` constructs a single 512-dim user vector from whatever data is available. There are three distinct types of user signal, each capturing a different level of style information.

**Individual wardrobe item photos** are the highest-fidelity signal. A user uploads a photo of a jacket they own; we embed it with CLIP's image encoder and store the result in `wardrobe_items.embedding`. These embeddings capture fine-grained visual detail — exact colorway, silhouette, texture — that text descriptions miss. Mean-pooling the wardrobe embeddings produces the "centre of mass" of the user's taste in CLIP space.

**Outfit photos** (a full look rather than a single item) capture a different signal: how pieces combine. Knowing a user owns a grey hoodie and black joggers separately doesn't tell you they wear them together, or that they style them with chunky sneakers rather than dress shoes. We treat outfit photos as style-level signals, not item-level ones. The recommended pipeline:

1. The uploaded image is sent to a vision-language model
2. The VLM generates a structured description: style tags, colour palette, formality level
3. The tags are surfaced to the user for verification and correction
4. Verified tags are embedded as text and stored as an outfit-level embedding

Individual wardrobe item photos compensate for information loss in this approach. The fine-grained visual detail that the VLM might not describe — fabric texture, exact colours — is already captured by item-level embeddings. The outfit photo's job is narrower: capture the combination-level aesthetic, which a description like "relaxed streetwear, earth tones, oversized silhouettes" encodes well.

**Style preference tags** (`'streetwear'`, `'ivy prep'`, `'formal'`) are stated rather than demonstrated preferences. CLIP's text encoder embeds them into the same 512-dim space as image embeddings, so they combine with visual embeddings by simple mean-pooling. They serve as a useful prior when the wardrobe is sparse, and can represent styles the user is building toward even before their wardrobe reflects them.

The three signal types have different reliability, which warrants weighted aggregation:

```python
# Weights are tuneable — these are reasonable starting values
wardrobe_item_embeddings  * 1.0   # demonstrated preference — strongest signal
outfit_embeddings         * 0.7   # combination-level style signal
preference_tag_embeddings * 0.5   # stated preference — weakest signal
```

The current implementation uses a flat mean-pool of wardrobe embeddings, with JSON-encoded style preferences as a cold-start fallback. The weighted multi-source approach is the natural next step once outfit photo upload is in the product.

```python
class RecommendationService:
    """
    Orchestrates the full recommendation pipeline:
      1. Build user embedding from wardrobe or style preferences
      2. Retrieve candidates from vector cache or dev catalog
      3. Rank candidates via UserTower + ItemTower cosine similarity
      4. Optionally generate LLM explanation
      5. Return ranked ProductRecommendation list
    """

    def __init__(self, s3_client, bucket: str, domain_name: str = "fashion") -> None:
        self._s3_client = s3_client
        self._bucket = bucket

        from app.services.domain_factory import get_domain
        self._domain = get_domain(domain_name)

        weights = _load_towers_from_s3(s3_client, bucket)
        if weights:
            self.user_tower = UserTower(weights["user_tower_W"])
            self.item_tower = ItemTower(weights["item_tower_W"])
        else:
            self.user_tower = UserTower()   # Xavier init
            self.item_tower = ItemTower()   # Xavier init

    async def _build_user_embedding(
        self,
        user_id: str,
        style_preferences: dict,
    ) -> np.ndarray:
        """
        Construct a 512-dim user vector.

        Strategy (in priority order):
          1. Query wardrobe_items for this user's CLIP embeddings.
          2. If any non-null 512-dim embeddings exist: mean-pool them, L2-normalize.
          3. Otherwise: encode style_preferences as a JSON text string.

        The mean-pool approach is simple but effective at cold start — it gives
        the user vector the "center of mass" of their wardrobe in CLIP space,
        which naturally represents their aesthetic.

        Returns:
            512-dim float32 unit vector (already passed through UserTower.forward).
        """
        from app.services.embedding_service import encode_text

        wardrobe_embeddings = []
        async with get_connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT embedding FROM wardrobe_items
                    WHERE user_id = %s AND embedding IS NOT NULL
                    """,
                    (user_id,),
                )
                rows = await cur.fetchall()

        for (emb_raw,) in rows:
            if emb_raw is not None:
                vec = (
                    emb_raw
                    if isinstance(emb_raw, np.ndarray)
                    else np.array(emb_raw, dtype=np.float32)
                )
                if vec.shape == (_EMBED_DIM,):
                    wardrobe_embeddings.append(vec)

        if wardrobe_embeddings:
            stacked = np.stack(wardrobe_embeddings, axis=0)   # (N, 512)
            mean_vec = stacked.mean(axis=0)                   # (512,)
            norm = np.linalg.norm(mean_vec)
            user_raw = mean_vec / norm if norm > 1e-8 else mean_vec
            logger.info(
                "Built user embedding from %d wardrobe items: user_id=%s",
                len(wardrobe_embeddings),
                user_id,
            )
        else:
            # Cold start — encode each style/color tag individually then mean-pool.
            #
            # Why not json.dumps(style_preferences)?
            # Encoding the raw JSON dict as a string is semantically noisy: the
            # JSON syntax (braces, quotes, colons, list brackets) contributes to
            # the CLIP embedding alongside the actual content. "streetwear" and
            # '{"styles": ["streetwear"]}' produce meaningfully different vectors.
            #
            # Encoding each tag independently and averaging keeps every tag in
            # the same region of CLIP space as the item vocabulary — which is
            # where items with those style attributes live.
            style_tags = (
                style_preferences.get("styles", [])
                + style_preferences.get("colors", [])
            )
            if style_tags:
                tag_vecs = np.stack([encode_text(tag) for tag in style_tags], axis=0)
                mean_vec = tag_vecs.mean(axis=0)
                norm = np.linalg.norm(mean_vec)
                user_raw = mean_vec / norm if norm > 1e-8 else mean_vec
                logger.info(
                    "No wardrobe embeddings — cold-start from %d style/color tags: user_id=%s",
                    len(style_tags),
                    user_id,
                )
            else:
                # Absolute fallback: user has no wardrobe and no style tags.
                # Use a broad fashion query rather than an empty/zero vector.
                user_raw = encode_text("casual everyday clothing")
                logger.info(
                    "No wardrobe embeddings and no style tags — using generic fallback: user_id=%s",
                    user_id,
                )

        return self.user_tower.forward(user_raw)

    def rank(
        self,
        user_embedding: np.ndarray,
        items: list[Item],
    ) -> list[tuple[Item, float]]:
        """
        Score each item and return (item, score) pairs sorted descending.

        For each item:
          1. Get or compute its embedding via FashionDomain.encode_item
          2. Project through ItemTower
          3. Score = dot(user_embedding, item_embedding)
             (equivalent to cosine similarity since both are unit vectors)

        Args:
            user_embedding: (512,) user vector, already projected through UserTower.
            items: Candidate items (may have None embeddings).

        Returns:
            List of (Item, float) sorted by score descending.
        """
        scored: list[tuple[Item, float]] = []
        for item in items:
            item_vec = self._domain.encode_item(item)       # use cached or encode on the fly
            projected = self.item_tower.forward(item_vec)
            score = float(np.dot(user_embedding, projected))
            scored.append((item, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored

    async def recommend(
        self,
        user_id: str,
        location: str,
        weather_context: dict,
        style_preferences: dict,
        top_k: int = 10,
        include_explanation: bool = False,
    ) -> list[ProductRecommendation]:
        """
        Full recommendation pipeline.

        Args:
            user_id: UUID string of the requesting user.
            location: Location string (for weather context in LLM prompt).
            weather_context: Dict with temp_c, condition, etc.
            style_preferences: User's style_preferences JSONB dict.
            top_k: Number of recommendations to return.
            include_explanation: Call LLM for natural-language explanation.

        Returns:
            Ranked list of ProductRecommendation objects.
        """
        from app.services import llm_service, vector_cache, dev_catalog_service
        from app.services.embedding_service import encode_text

        # Step 1: LLM generates a contextual search query
        query_text = await llm_service.generate_search_query(
            preferences=style_preferences,
            weather=weather_context,
        )

        # Step 2: Embed the search query
        query_embedding = encode_text(query_text)

        # Step 3: Check the vector cache
        cache_result = await vector_cache.lookup(query_embedding)
        if cache_result is not None:
            candidates, _ = cache_result
            logger.info("Using cached candidates: count=%d", len(candidates))
        else:
            # Step 4 (MISS): Fetch candidates from the Poshmark catalog
            candidates = await dev_catalog_service.search(
                query_embedding=query_embedding,
                limit=50,
            )
            # Step 5: Populate the cache for future requests
            await vector_cache.store(
                query_text=query_text,
                query_embedding=query_embedding,
                items=candidates,
                s3_client=self._s3_client,
                bucket=self._bucket,
            )

        if not candidates:
            logger.warning("No candidates found for user_id=%s — returning empty list", user_id)
            return []

        # Step 6: Build the user embedding
        user_embedding = await self._build_user_embedding(user_id, style_preferences)

        # Step 7: Rank candidates
        ranked = self.rank(user_embedding, candidates)[:top_k]

        # Step 8: Optional LLM explanation
        explanation = ""
        if include_explanation:
            top_dicts = [
                {"title": item.title, "price": item.price, "attributes": item.attributes}
                for item, _ in ranked[:3]
            ]
            explanation = await llm_service.generate_explanation(
                top_items=top_dicts,
                weather_context=weather_context,
                style_preferences=style_preferences,
            )

        # Step 9: Map to response model
        results = []
        for item, score in ranked:
            results.append(
                ProductRecommendation(
                    item_id=item.item_id,
                    title=item.title,
                    price=item.price,
                    product_url=item.product_url,
                    image_url=item.image_url or None,
                    similarity_score=round(score, 4),
                    attributes=item.attributes,
                    llm_explanation=explanation if include_explanation else None,
                )
            )

        logger.info(
            "Recommendation complete: user_id=%s top_k=%d query=%r",
            user_id,
            top_k,
            query_text,
        )
        return results
```

### Module-level singleton for RecommendationService

`RecommendationService.__init__` calls `_load_towers_from_s3`, which makes a real S3 `GetObject` (or receives a `NoSuchKey` error) on every instantiation. Constructing the service inside the request handler — as a naïve reading of §12 might suggest — means that S3 call happens on every request, including the common case where the model doesn't exist yet and the call always 404s.

Instead, initialize the service once at Lambda startup, exactly like the DB pool:

```python
# In app/services/recommendation_service.py — add at module level

import boto3
from app.core.config import config

# Module-level singleton — initialized once per Lambda instance.
# None until _init_recommendation_service() is called from main.py lifespan.
_recommendation_service: "RecommendationService | None" = None


def init_recommendation_service() -> None:
    """
    Create the module-level RecommendationService singleton.

    Call this from the FastAPI lifespan context manager (app startup),
    after the DB pool is initialized. Safe to call multiple times — only
    creates the service on the first call.
    """
    global _recommendation_service
    if _recommendation_service is not None:
        return
    s3_client = boto3.client("s3")
    bucket = config.weather_bucket_name
    _recommendation_service = RecommendationService(
        s3_client=s3_client, bucket=bucket
    )
    logger.info("RecommendationService initialized (bucket=%s)", bucket)


def get_recommendation_service() -> "RecommendationService":
    """Return the singleton, raising if not yet initialized."""
    if _recommendation_service is None:
        raise RuntimeError(
            "RecommendationService not initialized — "
            "call init_recommendation_service() at startup"
        )
    return _recommendation_service
```

Then call `init_recommendation_service()` from the existing lifespan in `app/main.py`:

```python
# In app/main.py — update the existing @asynccontextmanager lifespan

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await db_service.init_pool()
    from app.services.recommendation_service import init_recommendation_service
    init_recommendation_service()   # loads tower weights from S3 once
    yield
    # Shutdown
    await db_service.close_pool()
```

And use the singleton in the endpoint (shown in §12):

```python
from app.services.recommendation_service import get_recommendation_service

service = get_recommendation_service()   # no S3 call — returns cached instance
```

---

## 12. API Endpoints

**File:** `app/main.py` (additions)

Two new endpoints. Both follow the existing auth pattern in the file — every user-specific endpoint derives `user_id` from the JWT, not from the request body.

### Required imports

Add these to the existing import block in `app/main.py`:

```python
from fastapi import Depends, HTTPException, Query
from app.models.product import ProductRecommendation
from app.core import auth
```

### Request model

```python
class RecommendRequest(BaseModel):
    location: str
    include_explanation: bool = False
```

`user_id` is intentionally absent. Taking it as a client-supplied field would allow any authenticated user to request recommendations built from any other user's wardrobe — an IDOR (Insecure Direct Object Reference) vulnerability. The correct source is the JWT, extracted by the `auth.get_current_user_id` dependency exactly as the existing `/users/me` endpoints do.

### `/catalog/search`

```python
@app.get("/catalog/search")
async def catalog_search(
    q: str = Query(..., description="Text search query"),
    limit: int = Query(20, ge=1, le=100, description="Max results"),
    _: str = Depends(auth.get_current_user_id),   # auth required — compute-intensive endpoint
) -> dict:
    """
    Search catalog_items by semantic similarity to a text query.

    Embeds the query text using CLIP and performs ANN search on catalog_items.
    Intended for internal debugging and embedding quality verification — not a
    public-facing product endpoint. Authentication is enforced to prevent
    unauthenticated callers from triggering CLIP model loads.

    Note: This endpoint loads the CLIP model on first call (~2–3s cold start on EC2;
    not suitable for Lambda until the container image migration is complete).
    """
    from app.services.embedding_service import encode_text
    from app.services import dev_catalog_service

    logger.info("Catalog search: q=%r limit=%d", q, limit)
    try:
        query_embedding = encode_text(q)
        items = await dev_catalog_service.search(query_embedding, limit=limit)
        return {
            "query": q,
            "count": len(items),
            "results": [
                {
                    "item_id": item.item_id,
                    "title": item.title,
                    "price": item.price,
                    "product_url": item.product_url,
                    "image_url": item.image_url,
                    "attributes": item.attributes,
                }
                for item in items
            ],
        }
    except Exception:
        logger.error("Catalog search failed: q=%r", q, exc_info=True)
        raise HTTPException(status_code=500, detail="Catalog search failed")
```

### `/recommend-products`

```python
@app.post("/recommend-products")
async def recommend_products(
    request: RecommendRequest,
    user_id: str = Depends(auth.get_current_user_id),
) -> dict:
    """
    Full product recommendation pipeline.

    1. Fetches current weather for request.location
    2. Loads user preferences from DB
    3. Runs the recommendation pipeline:
       LLM query → CLIP embed → vector cache → ANN → two-tower reranking
    4. Returns ranked ProductRecommendation list

    Authentication required. user_id is derived from the JWT — the caller
    always receives recommendations based on their own wardrobe and preferences.

    The include_explanation flag adds ~1–2s latency (extra LLM call). Default False.
    """
    import asyncio
    from app.services import user_service, weather_service
    from app.services.recommendation_service import get_recommendation_service

    logger.info(
        "Recommend products: user_id=%s location=%s explain=%s",
        user_id,
        request.location,
        request.include_explanation,
    )

    try:
        # Fetch weather + user preferences concurrently
        weather_data, prefs = await asyncio.gather(
            weather_service.get_weather_data(request.location),
            user_service.get_user_preferences(user_id),
        )

        weather_context = {
            "temp_c": weather_data["current"]["temp_c"],
            "condition": weather_data["current"]["condition"]["text"],
            "location": request.location,
        }
        style_preferences = prefs.get("style_preferences", {})

        # Use the module-level singleton — no S3 call on the hot path
        service = get_recommendation_service()

        recommendations = await service.recommend(
            user_id=user_id,
            location=request.location,
            weather_context=weather_context,
            style_preferences=style_preferences,
            top_k=10,
            include_explanation=request.include_explanation,
        )

        return {
            "user_id": user_id,
            "location": request.location,
            "weather": weather_context,
            "count": len(recommendations),
            "recommendations": [r.model_dump() for r in recommendations],
        }

    except Exception:
        logger.error(
            "Recommendation failed: user_id=%s", user_id, exc_info=True
        )
        raise HTTPException(status_code=500, detail="Recommendation pipeline failed")
```

### Updated lifespan

The existing lifespan in `app/main.py` initializes the DB pool. Add the service initialization call immediately after:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await db_service.init_pool()
    from app.services.recommendation_service import init_recommendation_service
    init_recommendation_service()  # one S3 call (or 404) per Lambda warm instance
    yield
    # Shutdown
    await db_service.close_pool()
```

---

## 13. Backfill Script

**File:** `scripts/backfill_catalog_embeddings.py`

This one-time script embeds all `catalog_items` rows where `embedding IS NULL`. The text encoder runs on CPU — no GPU required — so it runs locally. The only constraint is DB connectivity: RDS is inside the VPC, so you need an SSH tunnel through the EC2 bastion.

```bash
# Terminal 1: open and hold the tunnel
ssh -L 5432:your-rds-endpoint.us-west-1.rds.amazonaws.com:5432 ec2-user@bastion-ip -N

# Terminal 2: run the backfill against the tunnelled endpoint
PYTHONPATH=. DATABASE_URL=postgresql://fitted:password@localhost:5432/fitted \
    python scripts/backfill_catalog_embeddings.py
```

The script is idempotent — it only fetches rows where `embedding IS NULL` — so it is safe to interrupt and resume.

```python
"""
Backfill CLIP text embeddings for catalog_items where embedding IS NULL.

Runs locally via SSH tunnel to RDS. No GPU required.

    # Open SSH tunnel first, then:
    PYTHONPATH=. DATABASE_URL=postgresql://...@localhost:5432/fitted \\
        python scripts/backfill_catalog_embeddings.py

Options:
    --batch-size N   Rows per DB commit (default: 100)
    --dry-run        Log without writing
    --limit N        Stop after N items (0 = unlimited)
"""

import argparse
import logging
import pathlib
import sys

import psycopg

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from app.core.config import config
from app.services.embedding_service import encode_text

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("backfill_catalog_embeddings")

_MODEL_VERSION = "clip-vit-b-32-text-v1"


def fetch_unembedded_batch(conn: psycopg.Connection, batch_size: int) -> list[dict]:
    """Return up to batch_size rows where embedding IS NULL."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT item_id, title, attributes
            FROM catalog_items
            WHERE embedding IS NULL
            LIMIT %s
            """,
            (batch_size,),
        )
        rows = cur.fetchall()

    return [
        {"item_id": row[0], "title": row[1] or "", "attributes": row[2] or {}}
        for row in rows
    ]


def embed_batch(items: list[dict]) -> list[tuple[str, list[float]]]:
    """
    Encode each item's title + brand + category text.
    Returns (item_id, embedding_as_list) pairs.
    """
    results = []
    for item in items:
        attrs = item["attributes"]
        parts = [item["title"]]
        if attrs.get("brand"):
            parts.append(attrs["brand"])
        if attrs.get("category"):
            parts.append(attrs["category"])
        text = " ".join(parts)
        vec = encode_text(text)
        results.append((item["item_id"], vec.tolist()))
    return results


def write_embeddings(
    conn: psycopg.Connection,
    embeddings: list[tuple[str, list[float]]],
) -> int:
    """
    UPDATE catalog_items SET embedding = %s, model_version = %s WHERE item_id = %s.
    Returns the number of rows updated.
    """
    with conn.cursor() as cur:
        for item_id, vec in embeddings:
            cur.execute(
                """
                UPDATE catalog_items
                SET embedding = %s::vector, model_version = %s
                WHERE item_id = %s
                """,
                (vec, _MODEL_VERSION, item_id),
            )
    conn.commit()
    return len(embeddings)


def run(args: argparse.Namespace) -> None:
    """Main loop: fetch batch → embed → write → repeat."""
    conn = psycopg.connect(config.database_url)
    total_done = 0

    try:
        while True:
            batch = fetch_unembedded_batch(conn, args.batch_size)
            if not batch:
                logger.info("No more unembedded items. Done.")
                break

            if args.dry_run:
                logger.info("[DRY RUN] Would embed %d items", len(batch))
                break

            embeddings = embed_batch(batch)
            written = write_embeddings(conn, embeddings)
            total_done += written
            logger.info("Embedded %d items (total so far: %d)", written, total_done)

            if args.limit and total_done >= args.limit:
                logger.info("Reached --limit=%d — stopping", args.limit)
                break

    finally:
        conn.close()

    logger.info("Backfill complete. Total embedded: %d", total_done)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill CLIP text embeddings for catalog_items"
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="Rows per DB commit (default: 100)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log without writing embeddings",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Stop after N items; 0 = no limit (default: 0)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    run(_parse_args())
```

---

## 14. Testing Strategy

Testing this pipeline requires mocking three heavy dependencies: `torch`/`open_clip`, the async psycopg3 pool, and S3. The test file is `tests/test_recommendation_service.py` and covers 31 cases across five test classes.

### Key insight: lazy imports and patch targets

`recommend()` imports its service dependencies inside the method body:

```python
async def recommend(self, ...):
    from app.services import llm_service, embedding_service, vector_cache, dev_catalog_service
    ...
```

This means you **cannot** patch `app.services.recommendation_service.llm_service.generate_search_query` — `llm_service` is not a module-level attribute of `recommendation_service`. Instead, patch at the **source module**:

```python
# ✗ Wrong — AttributeError at test time
patch("app.services.recommendation_service.llm_service.generate_search_query")

# ✓ Correct — patches the function in the module where it lives
patch("app.services.llm_service.generate_search_query")
```

The same rule applies to all lazily-imported services. The test file defines these as module-level constants for reuse:

```python
_PATCH_LLM     = "app.services.llm_service.generate_search_query"
_PATCH_ENCODE  = "app.services.embedding_service.encode_text"
_PATCH_CACHE_LOOKUP = "app.services.vector_cache.lookup"
_PATCH_CACHE_STORE  = "app.services.vector_cache.store"
_PATCH_CATALOG = "app.services.dev_catalog_service.search"
_PATCH_CONN    = "app.services.recommendation_service.get_connection"
_PATCH_EXPLAIN = "app.services.llm_service.generate_explanation"
```

### The `_make_service()` helper

`RecommendationService.__init__` calls `_load_towers_from_s3`, which makes a real S3 API call. Patch it out so tests get Xavier-initialised towers with no network I/O:

```python
from unittest.mock import MagicMock, patch
from app.services.recommendation_service import RecommendationService


def _make_service() -> RecommendationService:
    """Return a RecommendationService with Xavier-init towers (no real S3/torch call)."""
    # _load_towers_from_s3 returning None triggers Xavier cold start
    with patch(
        "app.services.recommendation_service._load_towers_from_s3", return_value=None
    ):
        svc = RecommendationService(s3_client=MagicMock(), bucket="test-bucket")
    return svc
```

### Mocking psycopg3

`conn.cursor()` is a **synchronous call** that returns an **async context manager** — not a coroutine. Mock it accordingly:

```python
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock


def _make_mock_conn(fetchall_return=None):
    mock_cur = AsyncMock()
    mock_cur.fetchall = AsyncMock(return_value=fetchall_return or [])
    mock_cur.execute = AsyncMock()

    mock_cur_ctx = MagicMock()
    mock_cur_ctx.__aenter__ = AsyncMock(return_value=mock_cur)
    mock_cur_ctx.__aexit__ = AsyncMock(return_value=False)

    mock_conn = MagicMock()
    mock_conn.cursor = MagicMock(return_value=mock_cur_ctx)  # sync call!
    mock_conn.commit = AsyncMock()

    return mock_conn, mock_cur


@asynccontextmanager
async def _mock_get_connection(mock_conn):
    yield mock_conn
```

### Testing towers directly (no mocks needed)

`UserTower` and `ItemTower` are pure numpy — no I/O, no torch at runtime:

```python
import numpy as np
from app.services.recommendation_service import UserTower, ItemTower

_DIM = 512


def test_xavier_init_produces_correct_shape():
    tower = UserTower()
    assert tower.W.shape == (_DIM, _DIM)
    assert tower.W.dtype == np.float32


def test_forward_returns_unit_vector():
    tower = UserTower()
    x = np.random.randn(_DIM).astype(np.float32)
    x /= np.linalg.norm(x)
    result = tower.forward(x)
    assert abs(np.linalg.norm(result) - 1.0) < 1e-5


def test_loaded_weights_stored_as_float32():
    weights = np.eye(_DIM, dtype=np.float64)  # pass float64
    tower = UserTower(weights=weights)
    assert tower.W.dtype == np.float32          # always cast to float32
```

### Testing `rank()` — identity weights for deterministic ordering

Xavier-random projection weights scramble cosine ordering, making rank tests flaky. Use **identity weights** so the projection is a no-op and the cosine similarity between embeddings is unchanged:

```python
def test_returns_sorted_descending():
    identity = np.eye(_DIM, dtype=np.float32)
    svc = _make_service()
    svc.user_tower = UserTower(weights=identity)
    svc.item_tower = ItemTower(weights=identity)

    # item_a: embedding identical to user → cosine similarity ≈ 1.0
    emb_a = _UNIT_VEC.copy()
    # item_b: first basis vector → cosine similarity = 1/sqrt(512) ≈ 0.044
    emb_b = np.zeros(_DIM, dtype=np.float32)
    emb_b[0] = 1.0

    item_a = _make_item("a", embedding=emb_a)
    item_b = _make_item("b", embedding=emb_b)

    ranked = svc.rank(_UNIT_VEC, [item_b, item_a])  # reversed order

    assert ranked[0][0].item_id == "a"
    assert ranked[0][1] > ranked[1][1]
```

### Testing `recommend()` — stacking patches with `ExitStack`

The full pipeline needs seven simultaneous patches. `contextlib.ExitStack` keeps this readable without deep nesting:

```python
from contextlib import ExitStack, asynccontextmanager
from unittest.mock import AsyncMock, patch


async def test_returns_top_k_results():
    svc = _make_service()
    candidates = [_make_item(str(i), embedding=_UNIT_VEC.copy()) for i in range(20)]
    mock_conn, _ = _make_mock_conn(fetchall_return=[])

    with ExitStack() as stack:
        stack.enter_context(patch(_PATCH_LLM,   new=AsyncMock(return_value="query")))
        stack.enter_context(patch(_PATCH_ENCODE, return_value=_UNIT_VEC.copy()))
        stack.enter_context(patch(_PATCH_CACHE_LOOKUP, new=AsyncMock(return_value=None)))
        stack.enter_context(patch(_PATCH_CATALOG,      new=AsyncMock(return_value=candidates)))
        stack.enter_context(patch(_PATCH_CACHE_STORE,  new=AsyncMock(return_value="id")))
        stack.enter_context(patch(_PATCH_CONN, return_value=_mock_get_connection(mock_conn)))

        result = await svc.recommend(
            user_id="u1",
            location="London",
            weather_context={"temp_c": 22.0, "condition": "Clear"},
            style_preferences={},
            top_k=5,
        )

    assert len(result) == 5


async def test_uses_cached_candidates_on_hit():
    """On cache hit, dev_catalog_service.search must NOT be called."""
    svc = _make_service()
    cached_items = [_make_item("cached", embedding=_UNIT_VEC.copy())]
    mock_conn, _ = _make_mock_conn(fetchall_return=[])
    mock_catalog = AsyncMock(return_value=[])

    with ExitStack() as stack:
        stack.enter_context(patch(_PATCH_LLM,   new=AsyncMock(return_value="query")))
        stack.enter_context(patch(_PATCH_ENCODE, return_value=_UNIT_VEC.copy()))
        stack.enter_context(patch(_PATCH_CACHE_LOOKUP,
                                  new=AsyncMock(return_value=(cached_items, "cache-id-123"))))
        stack.enter_context(patch(_PATCH_CATALOG, new=mock_catalog))
        stack.enter_context(patch(_PATCH_CONN, return_value=_mock_get_connection(mock_conn)))

        result = await svc.recommend(
            user_id="u1", location="LA",
            weather_context={"temp_c": 25.0, "condition": "Sunny"},
            style_preferences={},
        )

    mock_catalog.assert_not_called()
    assert result[0].item_id == "cached"
```

### Testing the singleton — patching `boto3.client`

`init_recommendation_service()` calls `import boto3; boto3.client("s3")` inside the function, so patch `boto3.client` at the top-level module (not through `recommendation_service`):

```python
def test_init_recommendation_service_creates_singleton():
    with patch("app.services.recommendation_service._load_towers_from_s3",
               return_value=None):
        with patch("boto3.client", return_value=MagicMock()):
            with patch("app.core.config.config") as mock_cfg:
                mock_cfg.weather_bucket_name = "test-bucket"
                init_recommendation_service()

    svc = get_recommendation_service()
    assert svc is not None


def test_init_recommendation_service_is_idempotent():
    """Calling init twice must not replace the existing singleton."""
    with patch("app.services.recommendation_service._load_towers_from_s3",
               return_value=None):
        with patch("boto3.client", return_value=MagicMock()):
            with patch("app.core.config.config") as mock_cfg:
                mock_cfg.weather_bucket_name = "test-bucket"
                init_recommendation_service()
                first  = get_recommendation_service()
                init_recommendation_service()  # second call — no-op
                second = get_recommendation_service()

    assert first is second
```

Use `setup_method` / `teardown_method` to reset `mod._recommendation_service = None` before and after each singleton test.

### Mocking CLIP in `test_embedding_service.py`

For the embedding service itself, mock at the `_load_model` boundary (not `open_clip` directly) and use `reset_model_for_testing()` to clear the singleton between tests:

```python
import numpy as np
import torch
from unittest.mock import MagicMock, patch
from app.services.embedding_service import reset_model_for_testing


@pytest.fixture(autouse=True)
def reset_clip_singleton():
    reset_model_for_testing()
    yield
    reset_model_for_testing()


def test_encode_text_returns_512_dim_unit_vector():
    mock_model = MagicMock()
    mock_model.encode_text.return_value = torch.ones(1, 512)
    mock_tokenizer = MagicMock(return_value=MagicMock())

    with patch("app.services.embedding_service._load_model",
               return_value=(mock_model, mock_tokenizer)):
        from app.services.embedding_service import encode_text
        result = encode_text("blue casual shirt")

    assert result.shape == (512,)
    assert result.dtype == np.float32
    assert abs(np.linalg.norm(result) - 1.0) < 1e-5
```

For all other services that call `encode_text` transitively, patch at the source:

```python
with patch("app.services.embedding_service.encode_text", return_value=_UNIT_VEC.copy()):
    result = await svc._build_user_embedding("user-1", prefs)
```

---

## 15. Late Interaction Reranking

The retrieval pipeline described above — CLIP text embedding → pgvector ANN search → two-tower ranking — is the production baseline. This section describes an optional upgrade to the ranking step that improves retrieval quality at the cost of additional storage.

### The limitation of single-vector retrieval

Every item in the catalog is represented by a single 512-dim vector — the "centre of mass" of all the visual and textual information in that item. A grey cashmere crewneck with a subtle cable-knit pattern has to compress its colour, texture, silhouette, category, and brand into one point in embedding space. A query for "grey knitwear" may not land close enough to that point even if the item is a perfect match, because the vector is simultaneously carrying signal about all of the item's other attributes.

### Patch embeddings

Vision transformers process images as sequences of patches. A ViT-B/32 splits a 224×224 image into a 7×7 grid — 49 patch tokens plus a `[CLS]` token. Each patch attends to every other patch through the transformer layers, so by the final layer each patch token has "seen" the whole image and carries locally-focused but globally-informed information.

**CLIP takes only the `[CLS]` token** as the image representation and discards the rest.

**ColPali (Contextualized Late Interaction over PaliGemma) keeps all patch tokens.** Using PaliGemma as the vision backbone and 448×448 input resolution, ColPali produces 784 patch vectors per image rather than one. Each patch embedding carries local visual information — this region of the fabric, this part of the collar — informed by the global context of the full image.

### MaxSim scoring

Late interaction scoring lets each query token find its best-matching image patch independently, then sums those scores:

```python
def maxsim(query_tokens: np.ndarray, patch_embeddings: np.ndarray) -> float:
    # query_tokens:     (Q, 128)   — one vector per query token
    # patch_embeddings: (784, 128) — one vector per image patch
    scores = query_tokens @ patch_embeddings.T   # (Q, 784)
    return float(scores.max(axis=1).sum())       # scalar
```

For a query like `"grey knitwear"`:
- The `"grey"` token scores highest against the patches that show grey fabric
- The `"knitwear"` token scores highest against the patches showing knit texture
- Each token finds its best match independently; the sum rewards items that satisfy multiple query aspects simultaneously

This is more accurate than single-vector retrieval for visually complex items because it does not require one vector to encode everything about the item simultaneously.

### Storage

The storage cost is the main practical concern:

| Precision | Per image (784 patches × 128 dims) | 100k items |
|---|---|---|
| float32 | 392 KB | 39 GB |
| int8 | 98 KB | 9.8 GB |
| binary | 12 KB | 1.2 GB |

Compare to the CLIP single-vector: 2 KB per item, 200 MB at 100k items. Patch embeddings are roughly 200× larger.

pgvector's HNSW index must fit in RAM. Indexing patch embeddings directly — one row per patch — would mean 100k × 784 = 78.4M index entries, on the order of 6–12 GB of RAM for the index alone. For this reason, **patch embeddings are stored but not indexed**. They are used only during the reranking stage, which operates on a small candidate set already retrieved by Stage 1.

The recommended storage is a `BYTEA` column on `catalog_items`, storing the patch array as a serialised numpy binary:

```sql
ALTER TABLE catalog_items ADD COLUMN patch_embeddings BYTEA;
-- write: cur.execute("UPDATE catalog_items SET patch_embeddings = %s WHERE item_id = %s",
--                    (patches.astype(np.float32).tobytes(), item_id))
-- read:  np.frombuffer(row["patch_embeddings"], dtype=np.float32).reshape(784, 128)
```

### The two-stage retrieval pipeline

The patch embeddings extend the pipeline with a reranking step that requires no changes to Stage 1:

```
Stage 1 — coarse retrieval (unchanged):
  query text → CLIP text encoder → 512-dim vector
  pgvector ANN → top-50 item_ids                                (~20ms)

Stage 2 — late interaction reranking (new):
  fetch patch_embeddings for top-50 items (PostgreSQL BYTEA)
  query text → token embeddings (CLIP text encoder, already warm)
  MaxSim(query_tokens, patch_embeddings) for each of 50 items
  re-rank → top-10                                              (+20–40ms)
```

Stage 1 uses the existing CLIP single-vector HNSW index with no changes. Stage 2 requires no index: brute-force MaxSim over 50 items is a handful of matrix multiplies, negligible compared to the fetch latency.

**Latency breakdown for Stage 2:**

| Step | Latency |
|---|---|
| Fetch 50 × patch embeddings from PostgreSQL | 5–15ms |
| Query token embedding (CLIP text encoder, warm) | 5–10ms |
| MaxSim scoring (numpy, 50 items) | 2–5ms |
| **Stage 2 total** | **~20–40ms** |

The patch embeddings are computed once per item during the backfill and never recomputed unless the model changes. At serving time, the VLM runs only to produce query token embeddings — a short text string, fast on a warm instance.

### Backfill for patch embeddings

A companion backfill script runs ColPali's vision encoder over each item's image URL. This is a GPU workload — PaliGemma (ColPali's backbone) requires a CUDA device — and it runs locally on the RTX 3080.

**VRAM budget on a 3080 (10 GB):**

| Precision | Model weights | Activations (448×448 image) | Total | Fits? |
|---|---|---|---|---|
| float16 | ~6 GB | ~2–3 GB | ~8–9 GB | Tight |
| int8 | ~3 GB | ~2 GB | ~5 GB | Comfortable |
| int4 | ~1.5 GB | ~2 GB | ~3.5 GB | Headroom |

int8 is the recommended default: it fits comfortably and the patch embedding quality difference is marginal for retrieval tasks.

**Network is the bottleneck, not the GPU.** At several hundred patches/second GPU throughput, the 3080 stalls waiting for image downloads if you fetch on-demand. The correct approach is to separate the phases:

```bash
# Phase 1: download all images to local SSD (fast, async, no GPU)
python scripts/download_catalog_images.py --output-dir /data/catalog_images

# Phase 2: run GPU processing from local disk — no network dependency during inference
python scripts/backfill_patch_embeddings.py --image-dir /data/catalog_images
```

**DB write path:** Rather than writing patch embeddings directly to RDS (which requires keeping the SSH tunnel open for the full job), write results to S3 and update the DB separately:

```python
# In backfill_patch_embeddings.py — conceptual
for item in fetch_items_without_patches(conn):
    image = load_from_disk(item["item_id"])
    patches = colpali_vision_encoder(image)          # (784, 128) float32, int8 quant
    s3_key = f"patches/{item['item_id']}.npy"
    s3_client.put_object(Bucket=bucket, Key=s3_key, Body=patches.tobytes())
    # DB update can happen in a separate lightweight pass
    log_completed(item["item_id"], s3_key)

# Separate pass: update catalog_items.patch_s3_key from the log
```

This decouples the GPU job from DB connectivity — if the SSH tunnel drops, the S3 writes are already committed and the DB update can be replayed.

Items without patch embeddings fall back to the Stage 1 ranking score, so the two-stage system degrades gracefully on a partially-backfilled catalog.

---

## 16. Future Architecture Directions

The pipeline described in this document is the production baseline. The following directions are worth tracking as the system matures and real interaction data accumulates.

### Bimodal wardrobes and multi-vector user representations

Mean-pooling wardrobe item embeddings works well when the user's style is unimodal. For users with genuinely bimodal wardrobes — half streetwear, half formal — the mean embedding lands in the middle of CLIP space, equidistant from both styles and representative of neither.

One experimental direction: rather than a single user vector, maintain patch-level embeddings for each wardrobe item, and score candidate items using a many-to-many variant of MaxSim. Each query-side vector is a patch from a wardrobe item; the document-side vectors are patches from the candidate item. The aggregation question — max over wardrobe items, mean, or attention-weighted — encodes different assumptions about what "matching a user's wardrobe" means and is itself a research problem.

At 10 wardrobe items × 784 patches × 784 candidate patches per request, the compute is on the order of 300M dot products — feasible on a beefy EC2 instance, outside the request budget for Lambda. Academic work on outfit compatibility (Polyvore dataset, Type-Aware Embedding, SCE-Net) covers directly relevant aggregation approaches.

### Generative retrieval

Generative recommendation reframes retrieval as a sequence-to-sequence task: given user context, generate item identifiers directly rather than searching a pre-built index. Papers like P5 and GPT4Rec; in production at scale at companies like ByteDance. The claimed advantage is that generation can reason over user history in ways that nearest-neighbour lookup cannot — though at the cost of requiring substantial interaction data to train well and higher inference latency. Worth understanding conceptually; not yet a practical baseline for most teams.

### LLM rerankers

The pipeline already has an LLM in the loop for query generation and explanation. A natural extension is using the LLM as a reranker at Stage 2: provide the top-10 candidates with their descriptions and user context, and ask the LLM to reorder them. This adds ~1–2s latency but produces ranking improvements and free natural-language explanations as a side effect. The `generate_explanation` function in `llm_service.py` is already half of this; the reranking step is the other half.

### Bradley-Terry preference reranker

The preference reranker is now implemented — see [Section 19](#19-the-bradley-terry-preference-reranker). Once users express pairwise preferences through the frontend UI, the Bradley-Terry model re-orders candidates based on individual taste signals that the two-tower model cannot capture from embeddings alone. At cold start (no preference pairs recorded yet), the reranker is a no-op and the two-tower order is returned unchanged.

---

## 17. Wardrobe CRUD Service

### Why wardrobe items are the highest-quality user signal

The recommendation pipeline builds a user embedding by mean-pooling the CLIP embeddings of every item in the user's wardrobe (see `_build_user_embedding` in `recommendation_service.py`). This is semantically superior to encoding raw style preference tags because the wardrobe items live in exactly the same CLIP embedding space as the catalog items being ranked — a navy blazer in the wardrobe and a navy blazer in the catalog will be close neighbors regardless of how the user described their style in text.

For this to work, users need a way to photograph and upload their existing clothes. The wardrobe CRUD service is the write path for those items.

### Schema recap

The `wardrobe_items` table was created in the initial DB migration:

```sql
CREATE TABLE IF NOT EXISTS wardrobe_items (
    item_id       UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id       UUID REFERENCES users(user_id) ON DELETE CASCADE,
    name          VARCHAR(255) NOT NULL,
    category      VARCHAR(50),                         -- tops / bottoms / outerwear / shoes / accessories
    image_s3_key  TEXT,                                -- wardrobe-images/{user_id}/{item_id}.jpg
    embedding     VECTOR(512),                         -- NULL until backfill script runs
    classification JSONB DEFAULT '{}',                 -- VLM-assigned attributes (future)
    tags          TEXT[] DEFAULT '{}',
    created_at    TIMESTAMPTZ DEFAULT NOW()
);
```

`embedding` is `NULL` on insert — it is populated later by a backfill script (same pattern as `catalog_items.embedding` in §13). The recommendation pipeline skips items with `NULL` embeddings when building the user vector, so the system degrades gracefully on partially-backfilled wardrobes.

### S3 image upload flow

Wardrobe images are routed through the API (browser → frontend → backend → S3) rather than uploaded directly from the browser. This keeps the S3 bucket private and avoids exposing AWS credentials to the client.

```
Browser
  │
  │  multipart/form-data (name, category, image file)
  ▼
FastHTML frontend  (/wardrobe/upload)
  │
  │  httpx multipart relay
  ▼
FastAPI backend  (POST /wardrobe)
  │
  ├──► S3: put_object("wardrobe-images/{user_id}/{item_id}.jpg")
  │         returns S3 key on success, None on failure (graceful degrade)
  │
  └──► PostgreSQL: INSERT INTO wardrobe_items (... image_s3_key ...)
```

The image key uses a predictable path: `wardrobe-images/{user_id}/{item_id}.jpg`. The `item_id` is generated with `uuid.uuid4()` _before_ the S3 upload so the path is deterministic regardless of insert order.

### `storage_service.py` additions

Two functions were added to `app/services/storage_service.py`:

```python
def upload_wardrobe_image(
    file_content: bytes,
    content_type: str,
    user_id: str,
    item_id: str,
) -> Optional[str]:
    """
    Upload a wardrobe item image to S3.
    Returns the S3 key on success, None if S3 is unavailable (graceful degrade).
    """
    key = f"wardrobe-images/{user_id}/{item_id}.jpg"
    s3_client.put_object(
        Bucket=WEATHER_BUCKET, Key=key, Body=file_content, ContentType=content_type
    )
    return key


def get_image_presigned_url(s3_key: str, expiry_seconds: int = 3600) -> Optional[str]:
    """
    Generate a presigned GET URL for a wardrobe image (1 h default expiry).
    Returns None if S3 is unavailable.
    """
    return s3_client.generate_presigned_url(
        "get_object",
        Params={"Bucket": WEATHER_BUCKET, "Key": s3_key},
        ExpiresIn=expiry_seconds,
    )
```

Both follow the same graceful-degrade pattern as the rest of `storage_service.py`: catch exceptions, log them, and return `None` rather than raising. The S3 key is stored in `wardrobe_items.image_s3_key`; when the frontend requests the wardrobe list, the backend converts each key to a presigned URL on-the-fly and returns it as `image_url` in the response.

### `wardrobe_service.py`

`app/services/wardrobe_service.py` mirrors the structure of `user_service.py` — async psycopg3 context managers, explicit `commit()` after mutations, debug/info logs on all operations:

```python
async def create_wardrobe_item(
    user_id: str, name: str, category: Optional[str], image_s3_key: Optional[str]
) -> dict:
    """Insert and return the new wardrobe item as a plain dict."""
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
    # ... unpack row, return dict


async def get_wardrobe_items(user_id: str) -> list[dict]:
    """Return all wardrobe items for a user, newest first."""

async def get_wardrobe_item(user_id: str, item_id: str) -> Optional[dict]:
    """Fetch a single item, enforcing ownership (WHERE user_id = %s AND item_id = %s)."""

async def delete_wardrobe_item(user_id: str, item_id: str) -> bool:
    """Delete a wardrobe item. Returns True if deleted, False if not found or wrong user."""
```

The ownership enforcement pattern in `delete_wardrobe_item` is important: the `WHERE` clause includes both `item_id = %s AND user_id = %s`. A user who knows another user's item UUID cannot delete it — the row simply won't match. This mirrors the IDOR protection in the `/recommend-products` endpoint.

### Pydantic models (`app/models/wardrobe.py`)

```python
class WardrobeItemCreate(BaseModel):
    name: str
    category: Optional[str] = None  # tops | bottoms | outerwear | shoes | accessories


class WardrobeItemResponse(BaseModel):
    item_id: str
    name: str
    category: Optional[str]
    image_url: Optional[str]   # presigned S3 GET URL (1 h expiry), None if no image
    tags: list[str]
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)
```

`image_url` is a presigned URL, not the raw S3 key — the key stays server-side.

### API endpoints

```
GET    /wardrobe              — list user's items; generates presigned URLs for each image
POST   /wardrobe              — multipart/form-data: name (Form), category (Form), image (File)
DELETE /wardrobe/{item_id}    — 204 on success, 404 if not found or wrong user
```

The `POST /wardrobe` endpoint signature:

```python
@app.post("/wardrobe", status_code=status.HTTP_201_CREATED)
async def add_wardrobe_item(
    name: str = Form(..., min_length=1, max_length=255),
    category: Optional[str] = Form(None),
    image: Optional[UploadFile] = File(None),
    user_id: str = Depends(auth.get_current_user_id),
) -> dict:
    ...
```

`user_id` comes from the JWT dependency — never from the request body. Even if a client sends a spoofed `user_id` field in the form, it is ignored.

---

## 18. Interaction Logging

### Why log interactions?

The two-tower model currently uses Xavier-initialized weights — it scores items based on CLIP-space geometry rather than observed user behavior. The Week 8 training pipeline will fine-tune those weights using implicit feedback: clicks, saves, and dismisses. Without an interaction log, there is nothing to train on.

The interaction log also serves a direct quality signal: a "dismiss" on an item is evidence of negative preference, and a "save" is a strong positive signal. These do not need to wait for model retraining to be useful — they feed the Bradley-Terry reranker in §19 alongside the explicit pairwise comparison signals.

### `user_interactions` table

Added to `scripts/db_migrate.py`:

```sql
CREATE TABLE IF NOT EXISTS user_interactions (
    interaction_id   UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id          UUID REFERENCES users(user_id) ON DELETE CASCADE,
    item_id          TEXT REFERENCES catalog_items(item_id),
    interaction_type VARCHAR NOT NULL CHECK (interaction_type IN ('click', 'save', 'dismiss')),
    weather_context  JSONB DEFAULT '{}',
    query_text       TEXT,
    created_at       TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_interactions_user ON user_interactions (user_id, created_at DESC);
```

`item_id` references `catalog_items`, not `wardrobe_items` — interactions are with recommended catalog products, not with the user's own clothes. `weather_context` and `query_text` are stored alongside each interaction so the training pipeline can use them as features (the recommendation was made under these conditions; the user clicked/dismissed).

### `POST /interactions` endpoint

```python
class InteractionCreate(BaseModel):
    item_id: str
    interaction_type: str   # 'click' | 'save' | 'dismiss'
    weather_context: dict = {}
    query_text: Optional[str] = None


@app.post("/interactions", status_code=status.HTTP_201_CREATED)
async def log_interaction(
    body: InteractionCreate,
    user_id: str = Depends(auth.get_current_user_id),
) -> dict:
    ...
    # INSERT INTO user_interactions (user_id, item_id, interaction_type, ...)
    return {"status": "logged"}
```

The endpoint validates `interaction_type` explicitly (FastAPI's enum support requires a separate `Enum` class; a simple check keeps the model lean). `user_id` is from the JWT — users can only log interactions for themselves.

### Frontend fire-and-forget pattern

Interaction logging is designed to be invisible to the user. The HTMX pattern:

```python
Button(
    "♥",
    hx_post="/log-interaction",
    hx_vals=f'{{"item_id":"{item["item_id"]}","interaction_type":"save"}}',
    hx_swap="none",   # ← no DOM update; response discarded
)
```

`hx_swap="none"` tells HTMX to make the request but ignore the response body. The button click records the signal without any visual feedback loop. This keeps the product card UI clean — the save/dismiss buttons are decorative from the user's perspective, consequential from the model's.

### `preference_pairs` table

```sql
CREATE TABLE IF NOT EXISTS preference_pairs (
    pair_id    UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id    UUID REFERENCES users(user_id) ON DELETE CASCADE,
    item_a_id  TEXT REFERENCES catalog_items(item_id),
    item_b_id  TEXT REFERENCES catalog_items(item_id),
    preferred  VARCHAR NOT NULL CHECK (preferred IN ('a', 'b')),
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_pairs_user ON preference_pairs (user_id);
```

Unlike `user_interactions`, preference pairs are explicit pairwise judgments — "given these two items side-by-side, I prefer A." The `preferred` column is a simple `'a'` or `'b'` string; the Bradley-Terry model (§19) interprets these signals without needing to know which item "should" win.

### `POST /preferences/pairs` endpoint

```python
class PreferencePairCreate(BaseModel):
    item_a_id: str
    item_b_id: str
    preferred: str   # 'a' | 'b'


@app.post("/preferences/pairs", status_code=status.HTTP_201_CREATED)
async def record_preference_pair(
    body: PreferencePairCreate,
    user_id: str = Depends(auth.get_current_user_id),
) -> dict:
    # INSERT INTO preference_pairs (user_id, item_a_id, item_b_id, preferred)
    return {"status": "recorded"}
```

Returns 201 with a minimal body. The frontend can call this fire-and-forget in the same `hx_swap="none"` pattern as interactions.

---

## 19. The Bradley-Terry Preference Reranker

### Why two-tower similarity is not enough

The two-tower model scores items by how close they are to the user's embedding in CLIP space. This is a measure of _objective relevance_ — the blazer matches your wardrobe's aesthetic. But two equally relevant items are not interchangeable: a user might consistently prefer slim-fit over relaxed-fit, or prefer a particular brand's colorways, or have a strong preference for items under a certain price threshold. None of these signals are visible to CLIP embeddings without explicit preference data.

The Bradley-Terry model provides a principled way to convert pairwise preference signals ("I prefer A over B") into item-level strength scores that can be blended with the cosine similarity ranking.

### The Bradley-Terry model

Given a set of pairwise comparisons, the Bradley-Terry model assigns each item a positive "strength" parameter $w_i$ such that:

$$P(i \text{ beats } j) = \frac{w_i}{w_i + w_j}$$

The strengths are fit by maximum likelihood. For a dataset where item $i$ beat item $j$ a total of $n_{ij}^{(i)}$ times out of $n_{ij}$ comparisons, the log-likelihood is:

$$\ell(w) = \sum_{i,j} n_{ij}^{(i)} \log w_i - n_{ij} \log(w_i + w_j)$$

The closed-form MLE has no closed form, but the MM (minorization-maximization) algorithm converges reliably with a simple iterative update rule.

### The MM algorithm

The MM update for item $i$ is:

$$w_i^{(\text{new})} = \frac{W_i}{\sum_{j \neq i} \frac{n_{ij}}{w_i + w_j}}$$

where $W_i$ is the total number of times item $i$ was preferred. This is guaranteed to increase the log-likelihood at each step and converges to the global MLE (assuming the comparison graph is connected).

```python
def _bradley_terry_mm(
    wins: dict[str, int],
    comparisons: dict[tuple[str, str], int],
    item_ids: list[str],
) -> dict[str, float]:
    n = len(item_ids)
    idx = {iid: i for i, iid in enumerate(item_ids)}

    # Symmetric comparison matrix: N[i, j] = total comparisons between i and j
    N = np.zeros((n, n), dtype=np.float64)
    for (a, b), cnt in comparisons.items():
        i, j = idx[a], idx[b]
        N[i, j] += cnt
        N[j, i] += cnt

    win_vec = np.array([wins.get(iid, 0) for iid in item_ids], dtype=np.float64)
    w = np.ones(n, dtype=np.float64)   # initialise all strengths to 1

    for iteration in range(_MM_MAX_ITER):   # max 100 iterations
        w_prev = w.copy()
        for i in range(n):
            denominator = np.sum(N[i] / (w[i] + w))
            if denominator > 0 and win_vec[i] > 0:
                w[i] = win_vec[i] / denominator

        w = w / w.sum() * n   # re-normalise to avoid numerical drift
        if np.max(np.abs(w - w_prev)) < _MM_TOL:   # tol = 1e-6
            break

    return {iid: float(w[idx[iid]]) for iid in item_ids}
```

No external dependencies — pure numpy. Items with zero wins keep their initial strength of 1 (a small positive constant that prevents division by zero while leaving them at the bottom of the preference ordering).

### Cold-start behaviour

```python
async def get_preference_scores(user_id: str) -> dict[str, float]:
    # ... query preference_pairs WHERE user_id = %s ...
    if not rows:
        return {}   # cold start — no reranking needed
    # ... fit Bradley-Terry, return {item_id: strength}
```

When a user has no preference pairs, `get_preference_scores` returns `{}`. The `rerank` function checks for this immediately:

```python
def rerank(
    ranked: list[tuple[Item, float]],
    preference_scores: dict[str, float],
    alpha: float = 0.3,
) -> list[tuple[Item, float]]:
    if not preference_scores or alpha == 0.0:
        return ranked   # no-op — returns two-tower order unchanged
    ...
```

This makes the reranker safe to call on every request without any conditional logic at the call site. The `recommend()` method always calls both functions:

```python
# Step 7: Rank candidates via two-tower cosine similarity
ranked = self.rank(user_embedding, candidates)

# Step 7.5: Preference reranking (no-op when user has no preference pairs)
pref_scores = await preference_reranker.get_preference_scores(user_id)
ranked = preference_reranker.rerank(ranked, pref_scores)[:top_k]
```

### Score blending

The `rerank` function normalises the Bradley-Terry strengths to $[0, 1]$ across the current candidate set, then blends them with the two-tower similarity score:

```python
def _normalise(s: float) -> float:
    return (s - min_s) / span if span > 1e-9 else 0.5

for item, sim_score in ranked:
    raw_strength = preference_scores.get(item.item_id)
    pref_score = _normalise(raw_strength) if raw_strength is not None else 0.5
    score = (1.0 - alpha) * sim_score + alpha * pref_score
```

Items not present in `preference_scores` receive a neutral score of 0.5 — they are neither promoted nor demoted relative to each other.

**Why `alpha=0.3`?** Preference data is sparse and noisy early on. A user's first few preference pairs might not be representative of their general taste. Weighting preferences at 30% gives them meaningful influence without overwhelming the quality signal the two-tower model provides for free. As more pairs accumulate and the Week 8 training pipeline fine-tunes the two-tower weights, you may want to adjust alpha empirically.

---

## 20. Frontend: Wardrobe, Preferences, and Product Cards

### The visibility problem

All of the pipeline work described in sections 1–19 is invisible to users unless there are UI surfaces to expose it. Before adding these surfaces:

- Users had no way to upload wardrobe items → the user embedding always fell back to cold-start style tags
- There was no way to express pairwise preferences → the Bradley-Terry reranker was always a no-op
- Recommendations appeared as plain text in the outfit suggestion → no visual product cards, no interaction signals

The frontend additions address all three gaps.

### Wardrobe page (`/wardrobe`)

The wardrobe page has two parts: an upload form and a gallery grid.

**Upload form:** The form uses `hx_encoding="multipart/form-data"` to send the image file alongside the text fields. The target is `#wardrobe-grid` with `hx_swap="afterbegin"` so the new card appears at the top of the gallery without a full page reload:

```python
Form(
    Input(type="text", name="name", placeholder="Item name (e.g. Navy Blazer)"),
    Select(
        Option("-- category --", value=""),
        Option("Tops", value="tops"),
        ...
        name="category",
    ),
    Input(type="file", name="image", accept="image/*"),
    Button("Add to wardrobe", type="submit"),
    hx_post="/wardrobe/upload",
    hx_target="#wardrobe-grid",
    hx_swap="afterbegin",
    hx_encoding="multipart/form-data",
)
```

**Gallery grid:** CSS grid of wardrobe cards. Each card includes an HTMX delete button:

```python
def wardrobe_card(item: dict) -> Div:
    return Div(
        Img(src=item.get("image_url") or "", cls="wardrobe-card-img"),
        Div(item["name"], cls="wardrobe-card-name"),
        Div(item.get("category") or "", cls="wardrobe-card-category"),
        Button(
            "✕",
            hx_delete=f"/wardrobe/{item['item_id']}",
            hx_confirm="Remove this item from your wardrobe?",
            hx_target="closest .wardrobe-card",
            hx_swap="outerHTML swap:0.3s",
        ),
        cls="wardrobe-card",
        id=f"wcard-{item['item_id']}",
    )
```

`hx_target="closest .wardrobe-card"` combined with `hx_swap="outerHTML swap:0.3s"` tells HTMX to replace the entire card element with the (empty) response from the delete endpoint, effectively removing it with a brief fade transition.

**Frontend sub-routes:**

- `GET /wardrobe`: fetches items from the backend, renders gallery + upload form
- `POST /wardrobe/upload`: HTMX fragment — relays multipart upload to backend, returns `wardrobe_card(item)` HTML
- `DELETE /wardrobe/{item_id}`: calls backend delete, returns empty string (HTMX removes the card element)

### Style preferences form (`/preferences`)

The preferences page maps to the `style_preferences` JSONB column on `user_preferences`. The four fields — styles, colors, occasions, avoid — correspond directly to the keys consumed by `generate_search_query` and `_build_user_embedding`:

```python
Form(
    Input(type="text", name="styles", placeholder="e.g. smart casual, streetwear, ivy prep"),
    Input(type="text", name="colors", placeholder="e.g. navy, white, olive"),
    Input(type="text", name="occasions", placeholder="e.g. work, weekend, formal"),
    Input(type="text", name="avoid", placeholder="e.g. loud prints, skinny fit"),
    Button("Save Preferences", type="submit"),
    hx_post="/preferences",
    hx_target="#prefs-feedback",
    hx_swap="innerHTML",
)
Div(id="prefs-feedback")
```

`hx_target="#prefs-feedback"` with `hx_swap="innerHTML"` means the success/error message from `POST /preferences` replaces the content of the feedback div inline, without a page reload. The user sees "Preferences saved." appear below the form.

### Product cards (home page)

After the outfit suggestion, a "Shop These Looks" section renders a horizontally scrollable row of product cards:

```python
def product_card(item: dict) -> Div:
    return Div(
        Img(src=item.get("image_url") or "", cls="product-card-img"),
        Div(item["title"], cls="product-card-title"),
        Div(f"${item['price']:.0f}", cls="product-card-price"),
        A("View on Poshmark", href=item["product_url"], target="_blank", cls="product-card-link"),
        Div(
            Button(
                "♥",
                hx_post="/log-interaction",
                hx_vals=f'{{"item_id":"{item["item_id"]}","interaction_type":"save"}}',
                hx_swap="none",
            ),
            Button(
                "✕",
                hx_post="/log-interaction",
                hx_vals=f'{{"item_id":"{item["item_id"]}","interaction_type":"dismiss"}}',
                hx_swap="none",
            ),
            cls="product-card-actions",
        ),
        cls="product-card",
    )
```

`hx_swap="none"` on both interaction buttons means the clicks are fire-and-forget: the request is sent, the response is discarded, and the UI does not change. The user sees a clean card with a save heart and a dismiss ✕; the recommendation pipeline records the signal silently.

### Navigation

The nav bar adds "Wardrobe" and "Prefs" links when the user is logged in:

```python
def nav_bar(session) -> Nav:
    logged_in = "access_token" in session
    links = [A("Home", href="/")]
    if logged_in:
        links += [A("Wardrobe", href="/wardrobe"), A("Prefs", href="/preferences")]
        links += [A("Logout", href="/logout")]
    else:
        links += [A("Login", href="/login"), A("Sign up", href="/register")]
    return Nav(*links, cls="nav-bar")
```

---

## 21. Image Embedding: Wardrobe Photo Encoding

### Why this matters

`_build_user_embedding` currently builds the user vector from style preference tags (`"streetwear"`, `"navy"`) when no wardrobe embeddings exist. Text tags are useful priors, but they are stated preferences — the user describing how they _want_ to dress rather than what they actually own.

Wardrobe photo embeddings are a much stronger signal. A photo of a navy blazer the user actually owns will embed in exactly the same CLIP space as the catalog items being ranked, producing a user vector that reflects demonstrated visual taste. Mean-pooling ten wardrobe item images gives the user tower a "centre of mass" in CLIP space that is far more informative than mean-pooling abstract style tags.

Until `encode_image` is live and wardrobe items have embeddings, the user tower always falls back to cold-start behaviour — effectively ignoring uploaded photos.

### The image encoder path

CLIP ViT-B/32 has two encoders in the same shared embedding space:
- **Text encoder** (~150MB unzipped): already live in `embedding_service.encode_text`
- **Image encoder** (~290MB unzipped): exceeds Lambda's 250MB zip limit

The image encoder therefore runs on the EC2 sidecar alongside the FastHTML frontend. Lambda endpoints that need image embeddings call it via internal HTTP, the same pattern described in `plan.md`.

### `encode_image` implementation

**File:** `app/services/embedding_service.py` (addition)

```python
def encode_image(url_or_s3_key: str) -> np.ndarray:
    """
    Encode an image using the CLIP ViT-B/32 image encoder.

    Accepts either a public URL or an S3 key (fetched via boto3).
    Preprocesses with CLIP's standard image transform (224×224 centre crop,
    normalisation), runs through the image encoder, L2-normalises.

    Returns a 512-dim float32 numpy array, unit norm — the same embedding
    space as encode_text. A photo of a "navy blazer" and the text
    "navy blazer" will be near neighbours after encoding.

    Only runs on EC2 (not Lambda — image encoder weights ~290MB exceed
    Lambda's 250MB unzipped package limit). Do not import this function
    in any code that runs on Lambda.

    Args:
        url_or_s3_key: Public URL (https://...) or S3 key (wardrobe-images/...).

    Returns:
        np.ndarray of shape (512,), dtype float32, unit norm.
    """
    import io
    import torch
    from PIL import Image
    import requests

    model, _, transform = _load_model_with_transform()  # extended loader that returns transform

    # Fetch image bytes
    if url_or_s3_key.startswith("http"):
        resp = requests.get(url_or_s3_key, timeout=10)
        resp.raise_for_status()
        image_bytes = resp.content
    else:
        import boto3
        from app.core.config import config
        s3 = boto3.client("s3")
        obj = s3.get_object(Bucket=config.weather_bucket_name, Key=url_or_s3_key)
        image_bytes = obj["Body"].read()

    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    image_tensor = transform(image).unsqueeze(0)   # (1, 3, 224, 224)

    with torch.no_grad():
        features = model.encode_image(image_tensor)
        features = features / features.norm(dim=-1, keepdim=True)

    embedding: np.ndarray = features.cpu().numpy().astype(np.float32)[0]
    logger.debug(
        "encode_image: key=%r shape=%s norm=%.4f",
        url_or_s3_key[:80],
        embedding.shape,
        float(np.linalg.norm(embedding)),
    )
    return embedding
```

`_load_model_with_transform()` is the extended version of `_load_model()` that also returns the preprocessing transform:

```python
def _load_model_with_transform():
    """Load CLIP model + image transform. Cached after first call."""
    global _model, _tokenizer, _transform
    if _model is not None:
        return _model, _tokenizer, _transform

    import open_clip, torch
    model, _, transform = open_clip.create_model_and_transforms(
        _CLIP_MODEL, pretrained=_CLIP_PRETRAINED
    )
    model.eval()
    tokenizer = open_clip.get_tokenizer(_CLIP_MODEL)
    _model, _tokenizer, _transform = model, tokenizer, transform
    return _model, _tokenizer, _transform
```

### Post-upload embedding trigger

After inserting a wardrobe item in `POST /wardrobe`, the backend schedules image encoding as a background task so the 201 response is not blocked:

```python
# In app/main.py — inside add_wardrobe_item, after wardrobe_service.create_wardrobe_item()

if image_s3_key:
    async def _embed_and_store(item_id: str, s3_key: str) -> None:
        try:
            from app.services.embedding_service import encode_image
            vec = encode_image(s3_key)
            async with db_service.get_connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        "UPDATE wardrobe_items SET embedding = %s::vector WHERE item_id = %s",
                        (vec.tolist(), item_id),
                    )
                    await conn.commit()
            logger.info("Wardrobe image embedded: item_id=%s", item_id)
        except Exception:
            logger.error("Failed to embed wardrobe image: item_id=%s", item_id, exc_info=True)

    asyncio.create_task(_embed_and_store(item["item_id"], image_s3_key))
```

The task is fire-and-forget: if encoding fails (network error, model not warm), the item row keeps `embedding = NULL` and `_build_user_embedding` degrades to style tag encoding. No user-facing impact.

### Backfill script

**File:** `scripts/backfill_wardrobe_embeddings.py`

Same pattern as `scripts/backfill_catalog_embeddings.py`:

```bash
# Open SSH tunnel to RDS, then:
PYTHONPATH=. DATABASE_URL=postgresql://fitted:password@localhost:5432/fitted \
    python scripts/backfill_wardrobe_embeddings.py
```

Idempotent: only fetches rows where `embedding IS NULL AND image_s3_key IS NOT NULL`. Processes in batches of 50, commits after each batch. Can be interrupted and resumed.

### Testing

Mock `_load_model_with_transform` and `requests.get` (or `boto3.client`) to avoid real network/model calls:

```python
def test_encode_image_returns_512_dim_unit_vector():
    mock_model = MagicMock()
    mock_features = torch.ones(1, 512)
    mock_model.encode_image.return_value = mock_features
    mock_transform = MagicMock(return_value=torch.zeros(3, 224, 224))

    with patch("app.services.embedding_service._load_model_with_transform",
               return_value=(mock_model, None, mock_transform)):
        with patch("requests.get") as mock_get:
            mock_get.return_value.content = _fake_jpeg_bytes()
            result = encode_image("https://example.com/jacket.jpg")

    assert result.shape == (512,)
    assert abs(np.linalg.norm(result) - 1.0) < 1e-5
```

---

## Summary of Files

| File | Purpose |
|---|---|
| `app/models/item.py` | `Item` dataclass — internal representation passed between services |
| `app/models/product.py` | `ProductRecommendation` — Pydantic response model |
| `app/services/embedding_service.py` | CLIP ViT-B/32 text encoder with lazy import + singleton cache |
| `app/services/domain.py` | `Domain` Protocol — domain-agnostic interface |
| `app/services/domains/fashion.py` | `FashionDomain` — encodes fashion queries and items |
| `app/services/domain_factory.py` | `get_domain(name)` factory |
| `app/services/vector_cache.py` | Semantic query cache using pgvector + S3 |
| `app/services/dev_catalog_service.py` | ANN search on Poshmark `catalog_items` |
| `app/services/llm_service.py` | `generate_search_query` + `generate_explanation` (additions) |
| `app/services/recommendation_service.py` | `UserTower`, `ItemTower`, `RecommendationService` |
| `app/main.py` | `GET /catalog/search`, `POST /recommend-products`, wardrobe, interaction, and preference-pair endpoints |
| `scripts/backfill_catalog_embeddings.py` | One-time script to embed the Poshmark catalog |
| `tests/test_embedding_service.py` | CLIP encoding tests (mock open_clip) |
| `tests/test_domain.py` | FashionDomain + factory tests |
| `tests/test_vector_cache.py` | Cache lookup/store tests (mock psycopg3 + S3) |
| `tests/test_dev_catalog_service.py` | ANN search + recency fallback tests |
| `tests/test_recommendation_service.py` | Tower init/forward, rank ordering, full pipeline |
| `app/models/wardrobe.py` | `WardrobeItemCreate`, `WardrobeItemResponse` Pydantic models |
| `app/services/wardrobe_service.py` | Async CRUD for `wardrobe_items` — create, list, get, delete |
| `app/services/preference_reranker.py` | Bradley-Terry MM reranker + `get_preference_scores` |
| `tests/test_wardrobe_service.py` | Wardrobe CRUD tests (mock psycopg3) |
| `tests/test_preference_reranker.py` | Bradley-Terry MM + rerank blending tests |
| `app/services/embedding_service.py` | `encode_image(url_or_s3_key)` — CLIP image encoder for wardrobe photos |
| `scripts/backfill_wardrobe_embeddings.py` | Backfill `wardrobe_items.embedding` from S3 images |
| `scripts/train_two_towers.py` | Train UserTower + ItemTower on interaction triplets; MLflow; S3 upload |
| `scripts/pretrain_item_tower.py` | Pre-train ItemTower with MSE reconstruction on catalog embeddings |
| `app/services/affiliate_service.py` | Amazon/ShopStyle/Rakuten link rewriting; `affiliate_clicks` DB; `/r/{click_id}` redirect |
| `tests/test_train_two_towers.py` | 20 tests — data loading, triplet construction, training, S3 upload |
| `tests/test_pretrain_item_tower.py` | 11 tests — embedding loading, training, S3 upload with UserTower preservation |
| `tests/test_affiliate_service.py` | 26 tests — URL rewriting, network detection, DB click tracking |

---

## 23. Affiliate Monetization: Link Rewriting and Click Tracking

### Motivation

Product cards link to external marketplaces. Without affiliate tags, these are revenue-free referrals. The affiliate layer rewrites product URLs to include network-specific tracking parameters before they reach the browser, and logs every click server-side for attribution.

Revenue model at 1K–10K users: $75–750/mo at 3% click-through, $100 AOV, 5% commission.

### Architecture

```
POST /recommend-products
    → rewrite_to_affiliate_url(product_url)   # Amazon | ShopStyle | Rakuten
    → record_affiliate_click(user, item, urls) → click_id (UUID)
    → response: { recommendations: [..., click_url: "/r/{click_id}"] }

User clicks product card
    → GET /r/{click_id}
    → resolve_and_record_click(click_id)       # marks clicked_at
    → 302 → affiliate_url
```

Product URLs are never sent to the browser with affiliate params embedded — this prevents adblockers from stripping them. The `/r/{click_id}` redirect is the only URL the browser sees.

### URL rewriting

**Amazon Associates** — appends `?tag=<affiliate_tag>` to `/dp/<ASIN>` product pages. Replaces existing `tag=` param if present. Only rewrites ASIN-pattern URLs; search pages are left unchanged.

**ShopStyle Collective** — appends `?pid=<publisher_id>&uid=<uuid>` to `shopstyle.com` URLs. The `uid` is a fresh UUID per click (ShopStyle uses it for deduplication).

**Rakuten** — wraps any merchant URL in a `click.linksynergy.com/deeplink?id=<site_id>&mid=<mid>&murl=<encoded>` redirect. Requires a per-merchant `mid` configured via `RAKUTEN_MID` env var.

All rewriters return `None` if the URL doesn't match their domain, enabling graceful fallthrough to the original URL.

### Configuration

All affiliate credentials are optional env vars. If unset, the corresponding network is disabled and the original URL passes through unchanged:

| Env var | Network | Example value |
|---|---|---|
| `AMAZON_AFFILIATE_TAG` | Amazon Associates | `fitted-20` |
| `SHOPSTYLE_PUBLISHER_ID` | ShopStyle Collective | `12345` |
| `RAKUTEN_SITE_ID` | Rakuten | `987654` |
| `RAKUTEN_MID` | Rakuten (per merchant) | `38723` |

### Database

The `affiliate_clicks` table stores pre-click state (affiliate URL resolved at recommendation time) and post-click state (`clicked_at` set on redirect):

```sql
CREATE TABLE affiliate_clicks (
    click_id      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id       UUID REFERENCES users ON DELETE CASCADE,
    item_id       TEXT REFERENCES catalog_items,
    original_url  TEXT NOT NULL,
    affiliate_url TEXT NOT NULL,
    network       VARCHAR NOT NULL DEFAULT 'none',
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    clicked_at    TIMESTAMPTZ   -- NULL until redirect fires
);
```

This gives you: click-through rate (CTR) = rows where `clicked_at IS NOT NULL` / total, broken down by network, item, user cohort, or time window.

---

## Document History

The following sections were added after the initial implementation draft:

- **Section 4 — The shared image-text space**: design rationale for CLIP's joint image-text embedding space, industry context, and the item representation strategy (feature extraction vs. direct embedding)
- **Section 4 — CLIP vs. SigLIP**: trade-offs between the InfoNCE and sigmoid training objectives and their practical implications for fashion retrieval quality
- **Section 11 — User signal sources**: individual wardrobe item photos, VLM-assisted outfit photo tagging with user verification, and style preference tags; weighted combination into the user embedding
- **Section 15 — Late Interaction Reranking**: ColPali patch embeddings, MaxSim scoring, storage trade-offs, and the two-stage retrieval pipeline; patch backfill runs locally on RTX 3080 with int8 quantization and a two-phase download-then-process approach
- **Section 16 — Future Architecture Directions**: bimodal wardrobe problem and multi-vector user representations, generative retrieval, LLM rerankers, and the Bradley-Terry preference reranker
- **Sections 17–20** — implementation of the surrounding user-signal infrastructure:
  - **Section 17**: Wardrobe CRUD service — S3 image upload flow, `wardrobe_service.py`, `storage_service` additions, API endpoints
  - **Section 18**: Interaction logging — `user_interactions` and `preference_pairs` schemas, fire-and-forget HTMX pattern for frontend interaction buttons
  - **Section 19**: Bradley-Terry preference reranker — MM algorithm (pure numpy), cold-start no-op, score blending with `alpha=0.3`, integration point in `RecommendationService.recommend()`
  - **Section 20**: Frontend surfaces — wardrobe gallery + upload form, style preferences form, product cards with save/dismiss buttons, nav bar updates
- **Section 23**: Affiliate monetization — `affiliate_service.py` URL rewriting (Amazon/ShopStyle/Rakuten), `affiliate_clicks` table, `GET /r/{click_id}` redirect, integration in `POST /recommend-products`
