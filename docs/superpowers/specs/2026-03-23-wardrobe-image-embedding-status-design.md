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

### 2. Service Layer — `wardrobe_service.embed_wardrobe_item`

New function in `app/services/wardrobe_service.py`:

```python
async def embed_wardrobe_item(item_id: str, s3_key: str) -> None:
```

Steps:
1. `UPDATE wardrobe_items SET embedding_status = 'embedding' WHERE item_id = %s`
2. Call `embedding_service.encode_image(s3_key)` → 512-dim L2-normalized vector
3. `UPDATE wardrobe_items SET embedding = %s, embedding_status = 'done' WHERE item_id = %s`
4. On any exception: `UPDATE wardrobe_items SET embedding_status = 'failed'`, then log and re-raise

The inline `_embed_wardrobe_image` function in `main.py` is removed. `POST /wardrobe` becomes:

```python
if image_s3_key:
    asyncio.create_task(
        wardrobe_service.embed_wardrobe_item(item["item_id"], image_s3_key)
    )
```

### 3. Status Endpoint

```
GET /wardrobe/{item_id}/status
```

- **Auth:** JWT required; ownership enforced (`WHERE item_id = %s AND user_id = %s`)
- **Response 200:**
  ```json
  { "item_id": "...", "embedding_status": "pending" | "embedding" | "done" | "failed" }
  ```
- **Response 404:** item not found or not owned by caller

### 4. Updated Response Shapes

`embedding_status` is added to the item dict returned by:
- `POST /wardrobe` (201 response)
- `GET /wardrobe` (each item in the list)
- `PUT /wardrobe/{item_id}` (updated item response)

This avoids an immediate poll on page load — the frontend has the initial status from the create/list response.

### 5. Frontend Polling (HTMX)

The wardrobe item card polls `GET /wardrobe/{item_id}/status` with `hx-trigger="every 2s"` while `embedding_status` is `pending` or `embedding`. Polling stops (trigger removed) when status reaches `done` or `failed`. No backend changes beyond the new endpoint.

## Files Changed

| File | Change |
|------|--------|
| `scripts/db_migrate.py` | Add `ALTER TABLE` for `embedding_status` |
| `app/services/wardrobe_service.py` | Add `embed_wardrobe_item`, update query returns to include `embedding_status` |
| `app/main.py` | Remove inline `_embed_wardrobe_image`, call `wardrobe_service.embed_wardrobe_item`; add `/wardrobe/{item_id}/status` endpoint; include `embedding_status` in wardrobe responses |
| `app/models/wardrobe.py` | Add `embedding_status` field to `WardrobeItemResponse` |
| `frontend/app.py` | Add HTMX polling badge to wardrobe item card |

## Out of Scope

- Retry logic for `failed` items (backfill script handles recovery)
- Webhook/SSE push (polling is sufficient at this scale)
- Embedding items that have no image
