# Recommendation System Implementation Plan

**Branch:** `feature/recommendation-system`
**Scope:** Week 5 + Week 6 (embeddings, domain protocol, vector cache, dev catalog, two-towers ranking, product recommendation endpoint)

**Out of scope:** SerpAPI live source, preference reranker (Bradley-Terry), interaction/preference logging endpoints, training pipeline (Week 8)

---

## Key Findings from Codebase

- `catalog_items` has `VECTOR(512)` + HNSW index — ready for embeddings
- `wardrobe_items` has `VECTOR(384)` — **mismatch** with plan.md's 512 (defer ALTER COLUMN)
- `query_cache` table does **not yet exist** in `db_migrate.py` — migration needed
- `open-clip-torch` is **not yet** in `pyproject.toml` (torch, numpy, Pillow already are)
- `pgvector` Python package already present
- `domains/` subdirectory does not yet exist under `app/services/`

---

## 1. Dependency Additions

Add to `pyproject.toml`:

```toml
"open-clip-torch>=2.26.1",
```

- `torch`, `numpy`, `Pillow` already present — no changes
- Add `"moto[s3]>=5.0.0"` to `[dependency-groups] dev` if not already there

---

## 2. Migration Note

### Tables already ready:
- `catalog_items` — `VECTOR(512)`, HNSW index ✅

### Table that must be added to `db_migrate.py`:

```sql
CREATE TABLE IF NOT EXISTS query_cache (
    cache_id        UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    query_hash      VARCHAR UNIQUE NOT NULL,
    query_text      TEXT,
    query_embedding VECTOR(512),
    s3_key          TEXT,
    expires_at      TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_query_cache_embedding
    ON query_cache USING hnsw (query_embedding vector_cosine_ops);

CREATE INDEX IF NOT EXISTS idx_query_cache_hash
    ON query_cache(query_hash);

CREATE INDEX IF NOT EXISTS idx_query_cache_expires
    ON query_cache(expires_at);
```

> **Do not run `db_migrate.py` without explicit confirmation.**

### Wardrobe dimension: ✅ fixed
`wardrobe_items.embedding` aligned to `VECTOR(512)` in this migration. All embeddings were NULL so no data loss. The ALTER TABLE block drops the HNSW index, alters the type, and recreates the index.

---

## 3. New Files

### `app/models/item.py`

Canonical `Item` dataclass shared across domains and the pipeline. Uses dataclass (not Pydantic) because numpy arrays don't serialize cleanly.

```python
@dataclass
class Item:
    item_id: str
    domain: str
    title: str
    price: float
    image_url: str
    product_url: str
    source: str
    embedding: Optional[np.ndarray]  # 512-dim float32 L2-normalized; None until embedded
    attributes: dict = field(default_factory=dict)
```

---

### `app/models/product.py`

Pydantic response model for the API.

```python
class ProductRecommendation(BaseModel):
    item_id: str
    title: str
    price: float
    product_url: str
    image_url: Optional[str] = None
    similarity_score: float
    attributes: dict = {}
    llm_explanation: Optional[str] = None  # only when include_explanation=True
```

---

### `app/services/embedding_service.py`

Wraps `open_clip` CLIP ViT-B/32 text encoder with lazy import (avoids torch at module-load time on Lambda).

```python
def encode_text(text: str) -> np.ndarray:
    """
    Encode text using CLIP ViT-B/32 text encoder.
    Returns: 512-dim float32 numpy array, L2-normalized.
    """

def reset_model_for_testing() -> None:
    """Reset cached model singletons — for test teardown."""
```

**Lambda size note:** CLIP ViT-B/32 is ~340MB; Lambda zip limit is 250MB. Resolution: deploy Lambda as a **container image** (10GB limit). The `template.yaml` SAM update is a separate infrastructure task. In the meantime, `/catalog/search` works on EC2 but may fail on Lambda until that migration. This is documented prominently in the module docstring.

---

### `app/services/domain.py`

Structural Protocol for domain-agnostic recommendation logic.

```python
@runtime_checkable
class Domain(Protocol):
    def encode_query(self, inputs: dict) -> np.ndarray: ...
    def encode_item(self, item: Item) -> np.ndarray: ...
    def parse_item(self, raw: dict) -> Item: ...
    def preference_context(self, query: dict, item: Item) -> dict: ...
```

Note: `fetch_candidates` is not part of the protocol — sourcing is injected separately via `DevCatalogService`.

---

### `app/services/domains/__init__.py`

Empty package marker.

---

### `app/services/domains/fashion.py`

```python
class FashionDomain:
    def encode_query(self, inputs: dict) -> np.ndarray:
        """
        Builds composite query text from query_text + weather_context + style_preferences,
        then calls encode_text(). Returns 512-dim unit vector.
        """

    def encode_item(self, item: Item) -> np.ndarray:
        """
        Returns item.embedding if not None (cache hit); otherwise builds
        title + brand + category + colors description and calls encode_text().
        """

    def parse_item(self, raw: dict) -> Item:
        """Converts a catalog_items DB row dict to Item, handles embedding list→ndarray."""

    def preference_context(self, query: dict, item: Item) -> dict:
        """Stub for Week 8 Bradley-Terry reranker."""
```

---

### `app/services/domain_factory.py`

```python
def get_domain(name: str | None = None) -> Domain:
    """
    Returns a Domain instance for the given name.
    Reads DOMAIN env var if name is None; defaults to 'fashion'.
    Raises ValueError for unknown domain names.
    """
```

---

### `app/services/vector_cache.py`

Semantic query cache using pgvector cosine distance on `query_cache` table. Serializes candidates to S3.

```python
async def lookup(
    query_embedding: np.ndarray,
    threshold: float = 0.85,
) -> Optional[tuple[list[Item], str]]:
    """
    ANN search on query_cache.query_embedding within cosine distance threshold.
    On HIT: loads candidate list from S3 via s3_key.
    Returns (items, cache_id) or None.
    """

async def store(
    query_text: str,
    query_embedding: np.ndarray,
    items: list[Item],
    s3_client,
    bucket: str,
    ttl_hours: int = 24,
) -> Optional[str]:
    """
    Serialize items to S3 (cache/query/{cache_id}.json), then upsert query_cache row.
    Returns cache_id string or None on failure.
    """
```

**Design note:** Item list stored in S3 (not inline in Postgres) to avoid bloating the HNSW index table with binary blobs. Cache HIT costs one extra S3 GetObject (~5ms) — acceptable at current scale.

---

### `app/services/dev_catalog_service.py`

Candidate source backed by Poshmark-seeded `catalog_items`.

```python
async def search(
    query_embedding: np.ndarray,
    limit: int = 50,
    domain: str = "fashion",
) -> list[Item]:
    """
    Primary: pgvector ANN on catalog_items.embedding (cosine distance ORDER BY).
    Fallback: ORDER BY last_seen DESC when no embedded rows exist.
    Returns list of Item objects.
    """
```

---

### `app/services/recommendation_service.py`

Two-Towers ranking + full pipeline orchestration.

```python
class UserTower:
    def __init__(self, weights: Optional[np.ndarray] = None) -> None:
        """Xavier-init 512×512 matrix if no weights provided."""

    def forward(self, user_embedding: np.ndarray) -> np.ndarray:
        """W @ v, L2-normalized. Returns 512-dim unit vector."""


class ItemTower:
    def __init__(self, weights: Optional[np.ndarray] = None) -> None:
        """Xavier-init 512×512 matrix if no weights provided."""

    def forward(self, item_embedding: np.ndarray) -> np.ndarray:
        """W @ v, L2-normalized. Returns 512-dim unit vector."""


class RecommendationService:
    def __init__(self, s3_client, bucket: str, domain_name: str = "fashion") -> None:
        """Loads towers from S3 models/two-towers/latest.pt; Xavier-init if absent."""

    async def _build_user_embedding(
        self, user_id: str, style_preferences: dict
    ) -> np.ndarray:
        """
        1. Query wardrobe_items for user's 512-dim embeddings.
        2. If found: mean-pool + L2-normalize.
        3. If not (or 384-dim mismatch): encode style_preferences JSON text.
        4. Pass through UserTower.forward().
        """

    def rank(
        self, user_embedding: np.ndarray, items: list[Item]
    ) -> list[tuple[Item, float]]:
        """
        For each item: encode via domain.encode_item() → ItemTower.forward().
        Score = dot(user_vec, item_vec) (cosine, both are unit vectors).
        Returns list sorted by score descending.
        """

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
        Pipeline:
        1. llm_service.generate_search_query(prefs, weather) → query string
        2. encode_text(query) → query_embedding
        3. vector_cache.lookup(query_embedding) → HIT: use cached; MISS: dev_catalog_service.search()
        4. On MISS: vector_cache.store(...)
        5. _build_user_embedding(user_id, prefs)
        6. rank(user_embedding, candidates)[:top_k]
        7. Optional: llm_service.generate_explanation(top_items, weather, prefs)
        8. Map to ProductRecommendation list
        """
```

**Numpy-only towers (not torch):** Single matrix multiply — numpy is sufficient and avoids torch cold-start overhead on the inference path. Training (Week 8) uses torch on EC2 and saves numpy-compatible tensors via `torch.save({'user_tower_W': tensor, 'item_tower_W': tensor})`.

---

### `scripts/backfill_catalog_embeddings.py`

One-time script to embed all `catalog_items` with `embedding IS NULL`.

```python
def fetch_unembedded_batch(conn, batch_size: int) -> list[dict]: ...
    """Returns up to batch_size rows: item_id, title, attributes."""

def embed_batch(items: list[dict]) -> list[tuple[str, list[float]]]: ...
    """Encodes title+brand+category text via encode_text(). Returns (item_id, embedding) pairs."""

def write_embeddings(conn, embeddings: list[tuple[str, list[float]]]) -> int: ...
    """UPDATE catalog_items SET embedding=%s, model_version='clip-vit-b-32-text-v1' WHERE item_id=%s."""

async def run(args: argparse.Namespace) -> None: ...
    """Main loop: fetch → embed → write, batch commit every --batch-size rows."""
```

---

## 4. Modified Files

### `app/services/llm_service.py`

Add two new async functions:

```python
async def generate_search_query(preferences: dict, weather: dict) -> str:
    """
    Prompt LLM to generate a 5-10 word clothing search query.
    Fallback: simple template string on LLM failure.
    """

async def generate_explanation(
    top_items: list[dict],
    weather_context: dict,
    style_preferences: dict,
) -> str:
    """
    2-3 sentence explanation of why these items were recommended.
    Called only when include_explanation=True.
    """
```

### `app/main.py`

Add two new endpoints:

```python
class RecommendRequest(BaseModel):
    user_id: str
    location: str
    include_explanation: bool = False

@app.get("/catalog/search")
async def catalog_search(
    q: str = Query(..., description="Text search query"),
    limit: int = Query(20, ge=1, le=100),
) -> dict:
    """Embed query text, ANN search catalog_items. Admin/debug — no auth in DEV_MODE."""

@app.post("/recommend-products")
async def recommend_products(request: RecommendRequest) -> dict:
    """Full recommendation pipeline returning list[ProductRecommendation]."""
```

### `scripts/db_migrate.py`

Add `query_cache` table SQL block after `catalog_items`. (Requires explicit confirmation before running.)

---

## 5. Implementation Order

```
Step 1:  app/models/item.py                       — no deps
Step 2:  app/models/product.py                    — no deps
Step 3:  app/services/embedding_service.py        — no deps (lazy torch import)
Step 4:  app/services/domain.py                   — depends: item.py
Step 5:  app/services/domains/__init__.py         — no deps
Step 6:  app/services/domains/fashion.py          — depends: item.py, embedding_service
Step 7:  app/services/domain_factory.py           — depends: fashion.py
Step 8:  scripts/db_migrate.py (query_cache)      — DB migration block (needs confirmation)
Step 9:  app/services/vector_cache.py             — depends: item.py, db_service
Step 10: app/services/dev_catalog_service.py      — depends: item.py, db_service
Step 11: llm_service.py additions                 — depends: existing llm infra
Step 12: app/services/recommendation_service.py   — depends: all above
Step 13: app/main.py additions                    — depends: recommendation_service
Step 14: scripts/backfill_catalog_embeddings.py   — depends: embedding_service, db_service
Step 15: tests (all)                              — written alongside each service
```

Steps 1–3 are fully parallel. Steps 4–7 can follow immediately after step 1.

---

## 6. Test Strategy

### Heavy dependency mocking

**torch / open_clip** — patch at `conftest.py` level:

```python
# tests/conftest.py — add this fixture
@pytest.fixture(autouse=False)
def mock_encode_text():
    """Returns a deterministic 512-dim unit vector without loading CLIP."""
    fake_vec = np.ones(512, dtype=np.float32) / np.sqrt(512)
    with patch("app.services.embedding_service.encode_text", return_value=fake_vec):
        yield fake_vec
```

Use `mock_encode_text` fixture in all tests that call encode_text indirectly. `test_embedding_service.py` specifically patches `open_clip.create_model_and_transforms` to test the encoding logic without downloading weights.

**psycopg3 pool** — reuse existing `_make_mock_conn` / `_patch_get_connection` pattern from `test_user_service.py` in:
- `test_vector_cache.py` → patches `app.services.vector_cache.get_connection`
- `test_dev_catalog_service.py` → patches `app.services.dev_catalog_service.get_connection`
- `test_recommendation_service.py` → patches `app.services.recommendation_service.get_connection`

**S3** — `MagicMock()` for `s3_client` passed directly (unit tests); `moto @mock_aws` for integration-style tests.

**Two-Towers** — pure numpy, no mocking needed. Test `UserTower`/`ItemTower` directly.

### Test files

```
tests/test_embedding_service.py       — shape (512,), unit norm, lazy import behavior
tests/test_domain.py                  — FashionDomain encode_query/item, parse_item, factory
tests/test_vector_cache.py            — cache miss, cache hit, store success, S3 failure
tests/test_dev_catalog_service.py     — ANN search path, recency fallback path
tests/test_recommendation_service.py — Xavier init, rank ordering, full recommend pipeline
tests/test_api_endpoints.py           — extend existing file: /catalog/search, /recommend-products
```

---

## 7. Non-Obvious Architectural Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Tower implementation | numpy matmul, not torch.nn.Linear | Lambda cold-start; inference is a single W@v per item; torch used only in training (Week 8 on EC2) |
| Item list storage | S3 JSON, pointer in query_cache | Avoids bloating pgvector HNSW table with binary blobs; ~5ms latency cost on cache HIT |
| Wardrobe dim | Fixed to VECTOR(512) in db_migrate.py | All embeddings were NULL — zero data loss; no fallback needed |
| /catalog/search auth | DEV_MODE bypass (same as existing endpoints) | Needs auth guard in prod; note in endpoint docstring |
| Lambda + CLIP | Document constraint, defer to container image | 340MB > 250MB limit; fix in infra branch, not here |
| User embedding cold start | encode(style_preferences JSON) | No wardrobe data yet; provides real signal for ranking from day one |
