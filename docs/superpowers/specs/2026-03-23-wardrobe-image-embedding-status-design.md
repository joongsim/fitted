# Wardrobe Image Embedding Status — Design Spec

**Date:** 2026-03-23
**Status:** Approved

## Summary

Two related changes:

1. **Service extraction** — move the inline `_embed_wardrobe_image` background task out of `main.py` into `wardrobe_service.py` so the logic is testable and reusable.
2. **Embedding status tracking** — add a persistent `embedding_status` column to `wardrobe_items` and a polling endpoint so the frontend can show users when their uploaded image has been embedded.

## Problem

The `POST /wardrobe` route contains an inline `asyncio.create_task` with a nested `_embed_wardrobe_image` function. This logic:
- Cannot be unit-tested without going through the HTTP layer
- Cannot be reused by backfill scripts or other callers
- Silently drops failures (fire-and-forget with no status feedback to the user)

## Design

### 1. DB Schema

Add one column to `wardrobe_items`:

```sql
ALTER TABLE wardrobe_items
  ADD COLUMN embedding_status VARCHAR(10) NOT NULL DEFAULT 'pending'
  CHECK (embedding_status IN ('pending', 'embedding', 'done', 'failed'));
```

State transitions:
- `pending` — default; no embedding attempted (includes items with no image)
- `embedding` — background task is actively running CLIP encoding
- `done` — embedding stored successfully
- `failed` — encode or DB write threw an exception; row retains `embedding = NULL`

Items without an image stay `pending` permanently (embedding is never triggered).

**Stale `embedding` rows:** If the process restarts while a task is in-flight, those rows stay `embedding` permanently. The backfill script should treat any row with `embedding_status = 'embedding'` as recoverable — reset them to `pending` before re-queuing.

### 2. Service Layer — `wardrobe_service.embed_wardrobe_item`

New function in `app/services/wardrobe_service.py`:

```python
async def embed_wardrobe_item(item_id: str, s3_key: str) -> None:
```

**Prerequisite fix in `embedding_service.py`:** `encode_image` currently reads the S3 bucket from `os.environ.get("WEATHER_BUCKET_NAME")`, which is the weather cache bucket. Wardrobe images are stored in `config.s3_bucket` (`AWS_S3_BUCKET`). Both occurrences (lines ~103 and ~125) must be changed to `from app.core.config import config; bucket = config.s3_bucket` before `encode_image` can correctly fetch wardrobe images.

Pseudocode structure of `embed_wardrobe_item`:

```python
async def embed_wardrobe_item(item_id: str, s3_key: str) -> None:
    from app.services.embedding_service import encode_image

    # Step 1 — mark as in-progress
    async with db_service.get_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("UPDATE wardrobe_items SET embedding_status = 'embedding' WHERE item_id = %s", (item_id,))
        await conn.commit()

    try:
        # Step 2 — encode (offload blocking call)
        loop = asyncio.get_running_loop()
        vec = await loop.run_in_executor(None, encode_image, s3_key)

        # Step 3 — store embedding
        async with db_service.get_connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "UPDATE wardrobe_items SET embedding = %s::vector, embedding_status = 'done' WHERE item_id = %s",
                    (vec.tolist(), item_id),
                )
            await conn.commit()

    except Exception as exc:
        # Step 4 — mark as failed; swallow cleanup errors
        try:
            async with db_service.get_connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("UPDATE wardrobe_items SET embedding_status = 'failed' WHERE item_id = %s", (item_id,))
                await conn.commit()
        except Exception:
            logger.error("embed_wardrobe_item: failed to set failed status: item_id=%s", item_id, exc_info=True)
        raise  # re-raise original exception
```

Import `asyncio` at module level in `wardrobe_service.py`.

### 3. Service Layer — `wardrobe_service.get_wardrobe_item_status`

New function in `app/services/wardrobe_service.py`:

```python
async def get_wardrobe_item_status(user_id: str, item_id: str) -> Optional[str]:
```

Runs: `SELECT embedding_status FROM wardrobe_items WHERE item_id = %s AND user_id = %s`

Returns the `embedding_status` string, or `None` if the item doesn't exist or is not owned by `user_id`.

### 4. Updated `create_wardrobe_item` return value (and all other service queries)

The `INSERT ... RETURNING` clause must include `embedding_status`:

```sql
INSERT INTO wardrobe_items (user_id, name, category, image_s3_key)
VALUES (%s, %s, %s, %s)
RETURNING item_id, name, category, image_s3_key, tags, created_at, embedding_status
```

Unpacking changes from `item_id, name_, cat, s3_key, tags, created_at = row` to `item_id, name_, cat, s3_key, tags, created_at, emb_status = row`. The returned dict gains `"embedding_status": emb_status`.

The same change (add `embedding_status` to SELECT columns and dict return) applies to:
- `get_wardrobe_items`
- `get_wardrobe_item`
- `update_wardrobe_item`

**Existing tests:** All existing tests in `tests/test_wardrobe_service.py` that construct mock rows as 6-tuples will break. Every mock row must be updated to a 7-tuple by appending `"pending"` (or the relevant status value) as the 7th element.

### 5. Route Changes in `main.py`

**`POST /wardrobe`:** Inside the route function, `wardrobe_service` is already imported via `from app.services import wardrobe_service` (following the lazy-import pattern used throughout `main.py`). Remove the inline `_embed_wardrobe_image` function and replace with the task call below. All three `WardrobeItemResponse(...)` constructor calls in `main.py` — in `POST /wardrobe`, `GET /wardrobe` (the list loop), and `PUT /wardrobe/{item_id}` — must pass `embedding_status=item["embedding_status"]` explicitly; the Pydantic default `"pending"` is not sufficient because existing items may have a different status.

```python
if image_s3_key:
    asyncio.create_task(
        wardrobe_service.embed_wardrobe_item(item["item_id"], image_s3_key)
    )
```

**New endpoint — JSON:**

```
GET /wardrobe/{item_id}/status
```

- Auth: JWT required; calls `wardrobe_service.get_wardrobe_item_status(user_id, item_id)`
- Response 200: `WardrobeItemStatusResponse`
- Response 404: item not found or not owned by caller
- When the request includes `HX-Request: true` header (HTMX request), return an HTML badge partial instead of JSON (see §7)

### 6. Models — `app/models/wardrobe.py`

**`WardrobeItemResponse`** gains one field, using `Literal` consistent with `CategoryType`:

```python
EmbeddingStatusType = Literal["pending", "embedding", "done", "failed"]

class WardrobeItemResponse(BaseModel):
    ...
    embedding_status: EmbeddingStatusType = "pending"
```

**New `WardrobeItemStatusResponse`:**

```python
class WardrobeItemStatusResponse(BaseModel):
    item_id: str
    embedding_status: EmbeddingStatusType
```

### 7. Frontend Polling (HTMX)

The wardrobe item card renders an embedding status badge. While `embedding_status` is `pending` or `embedding`, the badge element carries:

```html
hx-get="/wardrobe/{item_id}/status"
hx-trigger="every 2s"
hx-target="this"
hx-swap="outerHTML"
```

`GET /wardrobe/{item_id}/status` checks for the `HX-Request` header. When present, it returns an `HTMLResponse` (not JSON). Badge markup per status:

```html
<!-- pending or embedding — polling badge -->
<span id="embed-status-{item_id}"
      hx-get="/wardrobe/{item_id}/status"
      hx-trigger="every 2s"
      hx-target="this"
      hx-swap="outerHTML"
      class="badge badge-pending">
  ⏳ {status}
</span>

<!-- done — static badge, no polling attrs -->
<span id="embed-status-{item_id}" class="badge badge-done">✓ ready</span>

<!-- failed — static badge, no polling attrs -->
<span id="embed-status-{item_id}" class="badge badge-failed">✗ failed</span>
```

When status is `done` or `failed`, the returned badge omits `hx-trigger` and `hx-get`, so HTMX stops polling automatically after the next swap. The FastHTML frontend renders this badge via `NotStr` or `to_xml` as appropriate for the template engine in use.

## Files Changed

| File | Change |
|------|--------|
| `scripts/db_migrate.py` | Add `ALTER TABLE` for `embedding_status` |
| `app/services/embedding_service.py` | Fix `encode_image` S3 bucket: replace `WEATHER_BUCKET_NAME` with `config.s3_bucket` in both S3 fetch paths |
| `app/services/wardrobe_service.py` | Add `embed_wardrobe_item`, `get_wardrobe_item_status`; extend all RETURNING/SELECT queries to include `embedding_status`; update all tuple unpacking and return dicts |
| `app/main.py` | Remove inline `_embed_wardrobe_image`; call `wardrobe_service.embed_wardrobe_item`; pass `embedding_status` in all three `WardrobeItemResponse` calls (`POST`, `GET` list, `PUT`); add `GET /wardrobe/{item_id}/status` endpoint (JSON + HTMX HTML branch) |
| `app/models/wardrobe.py` | Add `EmbeddingStatusType`, `embedding_status` field to `WardrobeItemResponse`, add `WardrobeItemStatusResponse` |
| `frontend/app.py` | Add HTMX polling badge to wardrobe item card |

## Testing

**Update existing tests** in `tests/test_wardrobe_service.py`: all mock rows that are 6-tuples must become 7-tuples with `"pending"` appended as `embedding_status`.

**New tests** in `tests/test_wardrobe_service.py`:

- **`embed_wardrobe_item` — success path:** patch `app.services.embedding_service.encode_image` with a `MagicMock` returning a valid 512-dim float32 ndarray, and patch `asyncio.get_running_loop` to return a mock whose `run_in_executor` is an `AsyncMock` that calls `encode_image(s3_key)` synchronously and returns its result. Assert two DB updates occurred (`embedding_status = 'embedding'`, then `embedding = <vector>, embedding_status = 'done'`); assert `conn.commit()` was awaited after each UPDATE.
- **`embed_wardrobe_item` — failure path:** same patching approach, but `encode_image` raises `RuntimeError`. Assert DB was updated to `embedding_status = 'failed'`; assert `conn.commit()` was awaited; assert the original `RuntimeError` is re-raised (not a secondary exception from the cleanup path).
- **`get_wardrobe_item_status` — found:** mock returns a row; assert the correct status string is returned.
- **`get_wardrobe_item_status` — not found / wrong owner:** mock returns no row; assert `None` is returned.

**New route tests** in `tests/test_main.py`:

- `POST /wardrobe` with image: response body includes `embedding_status = 'pending'`.
- `GET /wardrobe/{item_id}/status`: 200 with correct JSON; 404 for unknown item.

## Out of Scope

- Retry logic for `failed` items (backfill script handles recovery; stale `embedding` rows should be reset to `pending` before re-queuing)
- SSE/WebSocket push (polling is sufficient at this scale)
- Embedding items that have no image
