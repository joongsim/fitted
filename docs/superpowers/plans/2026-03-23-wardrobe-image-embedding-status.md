# Wardrobe Image Embedding Status Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an `embedding_status` column to `wardrobe_items`, move the inline embedding task into `wardrobe_service`, fix the wrong S3 bucket in `encode_image`, and expose a polling endpoint so the frontend can show live embedding status.

**Architecture:** A new `embedding_status` VARCHAR column persists state across process restarts. `wardrobe_service.embed_wardrobe_item` owns the encode-and-store flow (offloading sync CLIP inference to a thread executor). The `GET /wardrobe/{item_id}/status` endpoint serves JSON or an HTML badge partial depending on the `HX-Request` header, and the frontend HTMX badge self-terminates polling on terminal states.

**Tech Stack:** Python 3.11, FastAPI, async psycopg3, pgvector, open-clip-torch, FastHTML/HTMX, pytest with `asyncio_mode=auto`

**Spec:** `docs/superpowers/specs/2026-03-23-wardrobe-image-embedding-status-design.md`

---

## File Map

| File | Action | What changes |
|------|--------|-------------|
| `scripts/db_migrate.py` | Modify | Add `ALTER TABLE` migration + rollback for `embedding_status` |
| `app/services/embedding_service.py` | Modify | Fix both S3 bucket lookups: `WEATHER_BUCKET_NAME` → `config.s3_bucket` |
| `app/models/wardrobe.py` | Modify | Add `EmbeddingStatusType`, `embedding_status` field to `WardrobeItemResponse`, new `WardrobeItemStatusResponse` |
| `app/services/wardrobe_service.py` | Modify | Add `asyncio` import; extend all 4 queries to return `embedding_status`; add `embed_wardrobe_item`, `get_wardrobe_item_status` |
| `app/main.py` | Modify | Remove inline `_embed_wardrobe_image`; update 3 `WardrobeItemResponse(...)` calls; add `GET /wardrobe/{item_id}/status` endpoint |
| `frontend/app.py` | Modify | Update `wardrobe_card` to include embedding status badge with HTMX polling |
| `tests/test_wardrobe_service.py` | Modify | Update all 6-tuple mock rows to 7-tuples; add 4 new test classes |
| `tests/test_api_endpoints.py` | Modify | Add tests for new status endpoint and `embedding_status` in POST response |

---

## Task 1: Fix `encode_image` S3 bucket bug

**Files:**
- Modify: `app/services/embedding_service.py:100-128`
- Test: `tests/test_embedding_service.py`

`encode_image` fetches from `WEATHER_BUCKET_NAME` instead of the app bucket (`config.s3_bucket`). This means wardrobe images (stored in `AWS_S3_BUCKET`) would 404. Fix both S3 paths before any other work — this is a prerequisite.

- [ ] **Step 1: Write failing test**

  Open `tests/test_embedding_service.py`. Add a test that mocks `boto3.client` and asserts the bucket used for an S3 key lookup matches `config.s3_bucket` (not `WEATHER_BUCKET_NAME`):

  ```python
  def test_encode_image_uses_app_bucket_not_weather_bucket():
      """encode_image must fetch from the app S3 bucket, not the weather cache bucket."""
      import os
      import numpy as np
      from unittest.mock import MagicMock, patch
      from io import BytesIO
      from PIL import Image

      # Build a minimal 224x224 RGB image in memory
      buf = BytesIO()
      Image.new("RGB", (224, 224)).save(buf, format="JPEG")
      image_bytes = buf.getvalue()

      mock_s3 = MagicMock()
      mock_s3.get_object.return_value = {"Body": MagicMock(read=lambda: image_bytes)}

      # Fake encode to avoid loading actual CLIP model
      fake_vec = np.ones(512, dtype=np.float32)
      fake_vec /= np.linalg.norm(fake_vec)

      with patch("boto3.client", return_value=mock_s3), \
           patch("app.services.embedding_service._load_model_and_transform") as mock_load, \
           patch("torch.no_grad"), \
           patch("app.core.config.config") as mock_cfg:

          mock_cfg.s3_bucket = "fitted-app-bucket"
          mock_cfg.embedding_service_url = None  # use local path

          mock_model = MagicMock()
          mock_features = MagicMock()
          mock_features.norm.return_value = MagicMock()
          mock_features.__truediv__ = MagicMock(return_value=mock_features)
          mock_features.cpu.return_value.numpy.return_value.astype.return_value = fake_vec.reshape(1, 512)
          mock_model.encode_image.return_value = mock_features
          mock_load.return_value = (mock_model, MagicMock(), MagicMock(return_value=MagicMock(unsqueeze=MagicMock(return_value=MagicMock()))))

          from app.services.embedding_service import encode_image
          encode_image("wardrobe-images/user/item.jpg")

      # Assert the correct bucket was used
      mock_s3.get_object.assert_called_once()
      call_kwargs = mock_s3.get_object.call_args
      assert call_kwargs[1]["Bucket"] == "fitted-app-bucket"
  ```

- [ ] **Step 2: Run to confirm it fails**

  ```bash
  cd C:/Projects/fitted && python -m pytest tests/test_embedding_service.py::test_encode_image_uses_app_bucket_not_weather_bucket -v
  ```
  Expected: FAIL — the current code uses `WEATHER_BUCKET_NAME`.

- [ ] **Step 3: Fix `embedding_service.py`**

  In `app/services/embedding_service.py`, there are two S3 fetch blocks (one in the `if _remote_url():` branch at ~line 103, one in the local path at ~line 125). In **both**, replace:

  ```python
  bucket = os.environ.get("WEATHER_BUCKET_NAME")
  ```

  with:

  ```python
  from app.core.config import config
  bucket = config.s3_bucket
  ```

- [ ] **Step 4: Run test to confirm it passes**

  ```bash
  python -m pytest tests/test_embedding_service.py::test_encode_image_uses_app_bucket_not_weather_bucket -v
  ```
  Expected: PASS

- [ ] **Step 5: Run full suite to check for regressions**

  ```bash
  make test 2>&1 | tail -20
  ```
  Expected: all previously-passing tests still pass.

- [ ] **Step 6: Commit**

  ```bash
  git add app/services/embedding_service.py tests/test_embedding_service.py
  git commit -m "fix: encode_image fetches from app S3 bucket, not weather bucket"
  ```

---

## Task 2: DB migration — add `embedding_status` column

**Files:**
- Modify: `scripts/db_migrate.py`

- [ ] **Step 1: Add migration SQL to `db_migrate.py`**

  Open `scripts/db_migrate.py`. Following the pattern used for the password-reset columns (see the bottom of the file), add:

  ```python
  # Migration: embedding_status column on wardrobe_items
  EMBEDDING_STATUS_MIGRATION_SQL = """
  ALTER TABLE wardrobe_items
    ADD COLUMN IF NOT EXISTS embedding_status VARCHAR(10) NOT NULL DEFAULT 'pending'
    CHECK (embedding_status IN ('pending', 'embedding', 'done', 'failed'));
  """

  EMBEDDING_STATUS_ROLLBACK_SQL = """
  ALTER TABLE wardrobe_items DROP COLUMN IF EXISTS embedding_status;
  """
  ```

  Wire it into the migration runner the same way the existing migrations are wired (look at how `ROLLBACK_SQL` and the main migration SQL are executed in the file's `main()` or equivalent function — follow that exact pattern).

- [ ] **Step 2: Commit**

  ```bash
  git add scripts/db_migrate.py
  git commit -m "feat: add embedding_status migration to wardrobe_items"
  ```

---

## Task 3: Update Pydantic models

**Files:**
- Modify: `app/models/wardrobe.py`
- Test: `tests/test_models.py` (or add inline assertions below)

- [ ] **Step 1: Write failing test**

  Add to `tests/test_models.py`:

  ```python
  def test_wardrobe_item_response_has_embedding_status():
      from app.models.wardrobe import WardrobeItemResponse
      from datetime import datetime, timezone
      item = WardrobeItemResponse(
          item_id="abc",
          name="Blazer",
          category=None,
          image_url=None,
          tags=[],
          created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
          embedding_status="pending",
      )
      assert item.embedding_status == "pending"

  def test_wardrobe_item_status_response_model():
      from app.models.wardrobe import WardrobeItemStatusResponse
      r = WardrobeItemStatusResponse(item_id="abc", embedding_status="done")
      assert r.embedding_status == "done"

  def test_embedding_status_rejects_invalid_value():
      from app.models.wardrobe import WardrobeItemResponse
      from datetime import datetime, timezone
      import pytest
      with pytest.raises(Exception):
          WardrobeItemResponse(
              item_id="x", name="x", category=None, image_url=None,
              tags=[], created_at=datetime(2024,1,1,tzinfo=timezone.utc),
              embedding_status="bogus",
          )
  ```

- [ ] **Step 2: Run to confirm it fails**

  ```bash
  python -m pytest tests/test_models.py -k "embedding_status" -v
  ```
  Expected: FAIL — field doesn't exist yet.

- [ ] **Step 3: Update `app/models/wardrobe.py`**

  ```python
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
  ```

- [ ] **Step 4: Run tests to confirm they pass**

  ```bash
  python -m pytest tests/test_models.py -k "embedding_status" -v
  ```
  Expected: PASS

- [ ] **Step 5: Commit**

  ```bash
  git add app/models/wardrobe.py tests/test_models.py
  git commit -m "feat: add EmbeddingStatusType and WardrobeItemStatusResponse models"
  ```

---

## Task 4: Update `wardrobe_service` queries + existing tests

**Files:**
- Modify: `app/services/wardrobe_service.py`
- Modify: `tests/test_wardrobe_service.py`

All four existing service functions (`create_wardrobe_item`, `get_wardrobe_items`, `get_wardrobe_item`, `update_wardrobe_item`) return row tuples that must now include `embedding_status`. The existing tests all use 6-tuples — they will break. Fix tests first, then update the service.

- [ ] **Step 1: Update all 6-tuple mock rows in `tests/test_wardrobe_service.py` to 7-tuples**

  Every mock row that ends with `_NOW` needs `"pending"` appended. Search for all occurrences:

  ```bash
  grep -n "_NOW)" tests/test_wardrobe_service.py
  ```

  For each match, change `(..., _NOW)` to `(..., _NOW, "pending")`. Example:

  ```python
  # Before
  row = (_ITEM_ID, "Navy Blazer", "outerwear", "wardrobe-images/u/i.jpg", ["navy"], _NOW)
  # After
  row = (_ITEM_ID, "Navy Blazer", "outerwear", "wardrobe-images/u/i.jpg", ["navy"], _NOW, "pending")
  ```

  Also add `assert result["embedding_status"] == "pending"` to `test_returns_dict_with_expected_fields` and `test_returns_updated_dict`.

- [ ] **Step 2: Run existing tests to confirm they now fail due to service mismatch**

  ```bash
  python -m pytest tests/test_wardrobe_service.py -v 2>&1 | head -40
  ```
  Expected: FAIL — unpacking errors because service still returns 6-tuples.

- [ ] **Step 3: Update all 4 service functions in `app/services/wardrobe_service.py`**

  Add `import asyncio` at the top of the file (just after `import logging`).

  For each function, extend the SQL and tuple unpacking:

  **`create_wardrobe_item`** — change RETURNING to:
  ```sql
  RETURNING item_id, name, category, image_s3_key, tags, created_at, embedding_status
  ```
  Change unpacking to:
  ```python
  item_id, name_, cat, s3_key, tags, created_at, emb_status = row
  ```
  Add to returned dict: `"embedding_status": emb_status`

  **`get_wardrobe_items`** — change SELECT to:
  ```sql
  SELECT item_id, name, category, image_s3_key, tags, created_at, embedding_status
  FROM wardrobe_items WHERE user_id = %s ORDER BY created_at DESC
  ```
  Change unpacking loop to:
  ```python
  for item_id, name, cat, s3_key, tags, created_at, emb_status in rows:
  ```
  Add `"embedding_status": emb_status` to each appended dict.

  **`get_wardrobe_item`** — change SELECT to include `embedding_status`, update unpacking and dict identically.

  **`update_wardrobe_item`** — change RETURNING to include `embedding_status`, update unpacking:
  ```python
  rid, name_, cat, s3_key, tags_, created_at, emb_status = row
  ```
  Add `"embedding_status": emb_status` to returned dict.

- [ ] **Step 4: Run existing tests to confirm they pass**

  ```bash
  python -m pytest tests/test_wardrobe_service.py -v
  ```
  Expected: all existing tests PASS.

- [ ] **Step 5: Commit**

  ```bash
  git add app/services/wardrobe_service.py tests/test_wardrobe_service.py
  git commit -m "feat: include embedding_status in all wardrobe_service query returns"
  ```

---

## Task 5: Add `embed_wardrobe_item` service function

**Files:**
- Modify: `app/services/wardrobe_service.py`
- Modify: `tests/test_wardrobe_service.py`

- [ ] **Step 1: Write failing tests**

  Add this class to `tests/test_wardrobe_service.py`:

  ```python
  # ---------------------------------------------------------------------------
  # embed_wardrobe_item
  # ---------------------------------------------------------------------------

  class TestEmbedWardrobeItem:
      _ITEM_ID_STR = str(_ITEM_ID)
      _S3_KEY = "wardrobe-images/user/item.jpg"

      def _make_executor_patch(self, vec, raise_exc=None):
          """
          Patch asyncio.get_running_loop so run_in_executor calls encode_image
          synchronously and returns vec (or raises raise_exc).
          """
          import asyncio
          mock_loop = MagicMock()
          if raise_exc:
              async def fake_executor(executor, fn, *args):
                  raise raise_exc
          else:
              async def fake_executor(executor, fn, *args):
                  return vec
          mock_loop.run_in_executor = fake_executor
          return patch("asyncio.get_running_loop", return_value=mock_loop)

      @pytest.mark.asyncio
      async def test_success_sets_embedding_and_done_status(self):
          import numpy as np
          vec = np.ones(512, dtype=np.float32)
          vec /= np.linalg.norm(vec)

          conn1, cur1 = _make_mock_conn()  # SET embedding
          conn2, cur2 = _make_mock_conn()  # SET done
          calls = iter([_mock_get_connection(conn1), _mock_get_connection(conn2)])

          with patch(_PATCH_CONN, side_effect=lambda: next(calls)), \
               self._make_executor_patch(vec):
              await wardrobe_service.embed_wardrobe_item(self._ITEM_ID_STR, self._S3_KEY)

          # First DB call: SET embedding_status = 'embedding'
          sql1, params1 = cur1.execute.call_args[0]
          assert "embedding_status" in sql1
          assert "'embedding'" in sql1 or "embedding" in str(params1)
          conn1.commit.assert_awaited_once()

          # Second DB call: SET embedding + done
          sql2, params2 = cur2.execute.call_args[0]
          assert "embedding_status" in sql2
          assert "done" in sql2 or "done" in str(params2)
          assert "::vector" in sql2
          conn2.commit.assert_awaited_once()

      @pytest.mark.asyncio
      async def test_failure_sets_failed_status_and_reraises(self):
          conn1, cur1 = _make_mock_conn()  # SET embedding
          conn2, cur2 = _make_mock_conn()  # SET failed
          calls = iter([_mock_get_connection(conn1), _mock_get_connection(conn2)])

          exc = RuntimeError("CLIP exploded")
          with patch(_PATCH_CONN, side_effect=lambda: next(calls)), \
               self._make_executor_patch(None, raise_exc=exc):
              with pytest.raises(RuntimeError, match="CLIP exploded"):
                  await wardrobe_service.embed_wardrobe_item(self._ITEM_ID_STR, self._S3_KEY)

          # Cleanup DB call: SET embedding_status = 'failed'
          sql2, _ = cur2.execute.call_args[0]
          assert "failed" in sql2
          conn2.commit.assert_awaited_once()

      @pytest.mark.asyncio
      async def test_cleanup_error_is_swallowed_original_reraises(self):
          """If the failed-status UPDATE itself throws, original exception still propagates."""
          import numpy as np

          conn1, _ = _make_mock_conn()  # SET embedding

          # Cleanup connection raises
          bad_conn = MagicMock()
          bad_conn.commit = AsyncMock(side_effect=Exception("DB gone"))
          bad_conn_ctx = MagicMock()
          bad_cur = AsyncMock()
          bad_cur.execute = AsyncMock()
          bad_cur_ctx = MagicMock()
          bad_cur_ctx.__aenter__ = AsyncMock(return_value=bad_cur)
          bad_cur_ctx.__aexit__ = AsyncMock(return_value=False)
          bad_conn.cursor = MagicMock(return_value=bad_cur_ctx)

          @asynccontextmanager
          async def _bad_conn_ctx():
              yield bad_conn

          calls = iter([_mock_get_connection(conn1), _bad_conn_ctx()])
          original_exc = RuntimeError("encode failed")

          with patch(_PATCH_CONN, side_effect=lambda: next(calls)), \
               self._make_executor_patch(None, raise_exc=original_exc):
              with pytest.raises(RuntimeError, match="encode failed"):
                  await wardrobe_service.embed_wardrobe_item(self._ITEM_ID_STR, self._S3_KEY)
  ```

- [ ] **Step 2: Run to confirm they fail**

  ```bash
  python -m pytest tests/test_wardrobe_service.py::TestEmbedWardrobeItem -v
  ```
  Expected: FAIL — function doesn't exist.

- [ ] **Step 3: Implement `embed_wardrobe_item` in `app/services/wardrobe_service.py`**

  Add at the end of the file (the `asyncio` import was added in Task 4):

  ```python
  async def embed_wardrobe_item(item_id: str, s3_key: str) -> None:
      """
      Encode the wardrobe item image at s3_key with CLIP and persist the embedding.

      Sets embedding_status to 'embedding' before starting, then 'done' on
      success or 'failed' on any exception. Offloads the synchronous encode_image
      call to a thread pool executor to avoid blocking the event loop.
      """
      from app.services.embedding_service import encode_image

      async with get_connection() as conn:
          async with conn.cursor() as cur:
              await cur.execute(
                  "UPDATE wardrobe_items SET embedding_status = 'embedding' WHERE item_id = %s",
                  (item_id,),
              )
          await conn.commit()

      try:
          loop = asyncio.get_running_loop()
          vec = await loop.run_in_executor(None, encode_image, s3_key)

          async with get_connection() as conn:
              async with conn.cursor() as cur:
                  await cur.execute(
                      "UPDATE wardrobe_items SET embedding = %s::vector, embedding_status = 'done' WHERE item_id = %s",
                      (vec.tolist(), item_id),
                  )
              await conn.commit()

      except Exception:
          try:
              async with get_connection() as conn:
                  async with conn.cursor() as cur:
                      await cur.execute(
                          "UPDATE wardrobe_items SET embedding_status = 'failed' WHERE item_id = %s",
                          (item_id,),
                      )
                  await conn.commit()
          except Exception:
              logger.error(
                  "embed_wardrobe_item: failed to set failed status: item_id=%s",
                  item_id,
                  exc_info=True,
              )
          raise
  ```

- [ ] **Step 4: Run tests to confirm they pass**

  ```bash
  python -m pytest tests/test_wardrobe_service.py::TestEmbedWardrobeItem -v
  ```
  Expected: all 3 tests PASS.

- [ ] **Step 5: Run full wardrobe service tests**

  ```bash
  python -m pytest tests/test_wardrobe_service.py -v
  ```
  Expected: all tests PASS.

- [ ] **Step 6: Commit**

  ```bash
  git add app/services/wardrobe_service.py tests/test_wardrobe_service.py
  git commit -m "feat: add embed_wardrobe_item service function with status tracking"
  ```

---

## Task 6: Add `get_wardrobe_item_status` service function

**Files:**
- Modify: `app/services/wardrobe_service.py`
- Modify: `tests/test_wardrobe_service.py`

- [ ] **Step 1: Write failing tests**

  Add to `tests/test_wardrobe_service.py`:

  ```python
  # ---------------------------------------------------------------------------
  # get_wardrobe_item_status
  # ---------------------------------------------------------------------------

  class TestGetWardrobeItemStatus:
      @pytest.mark.asyncio
      async def test_found_returns_status_string(self):
          mock_conn, _ = _make_mock_conn(fetchone_return=("done",))

          with patch(_PATCH_CONN, return_value=_mock_get_connection(mock_conn)):
              result = await wardrobe_service.get_wardrobe_item_status(
                  _USER_ID, str(_ITEM_ID)
              )

          assert result == "done"

      @pytest.mark.asyncio
      async def test_not_found_returns_none(self):
          mock_conn, _ = _make_mock_conn(fetchone_return=None)

          with patch(_PATCH_CONN, return_value=_mock_get_connection(mock_conn)):
              result = await wardrobe_service.get_wardrobe_item_status(
                  _USER_ID, "nonexistent-id"
              )

          assert result is None

      @pytest.mark.asyncio
      async def test_query_filters_by_both_item_id_and_user_id(self):
          mock_conn, mock_cur = _make_mock_conn(fetchone_return=None)

          with patch(_PATCH_CONN, return_value=_mock_get_connection(mock_conn)):
              await wardrobe_service.get_wardrobe_item_status(_USER_ID, str(_ITEM_ID))

          params = mock_cur.execute.call_args[0][1]
          assert str(_ITEM_ID) in params
          assert _USER_ID in params
  ```

- [ ] **Step 2: Run to confirm they fail**

  ```bash
  python -m pytest tests/test_wardrobe_service.py::TestGetWardrobeItemStatus -v
  ```
  Expected: FAIL — function doesn't exist.

- [ ] **Step 3: Implement `get_wardrobe_item_status` in `app/services/wardrobe_service.py`**

  Add after `embed_wardrobe_item`:

  ```python
  async def get_wardrobe_item_status(user_id: str, item_id: str) -> Optional[str]:
      """
      Return the embedding_status for a wardrobe item, enforcing ownership.

      Returns the status string ('pending', 'embedding', 'done', 'failed'),
      or None if the item doesn't exist or is not owned by user_id.
      """
      async with get_connection() as conn:
          async with conn.cursor() as cur:
              await cur.execute(
                  "SELECT embedding_status FROM wardrobe_items WHERE item_id = %s AND user_id = %s",
                  (item_id, user_id),
              )
              row = await cur.fetchone()

      if row is None:
          return None
      return row[0]
  ```

- [ ] **Step 4: Run tests to confirm they pass**

  ```bash
  python -m pytest tests/test_wardrobe_service.py::TestGetWardrobeItemStatus -v
  ```
  Expected: all 3 tests PASS.

- [ ] **Step 5: Run full test suite**

  ```bash
  make test 2>&1 | tail -20
  ```
  Expected: all tests pass.

- [ ] **Step 6: Commit**

  ```bash
  git add app/services/wardrobe_service.py tests/test_wardrobe_service.py
  git commit -m "feat: add get_wardrobe_item_status service function"
  ```

---

## Task 7: Update `main.py` — routes and response shapes

**Files:**
- Modify: `app/main.py`
- Modify: `tests/test_api_endpoints.py`

Three changes here: remove inline embedding task, add `embedding_status` to 3 response constructors, add the new status endpoint.

- [ ] **Step 1: Write failing tests**

  Add to `tests/test_api_endpoints.py` (follow the existing pattern for mocking auth and wardrobe_service):

  ```python
  # ── embedding_status in POST /wardrobe response ──────────────────────────

  def test_post_wardrobe_response_includes_embedding_status(client, valid_jwt_token):
      """POST /wardrobe response must include embedding_status field."""
      from unittest.mock import patch, AsyncMock
      from datetime import datetime, timezone

      item_dict = {
          "item_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
          "name": "Test Blazer",
          "category": "outerwear",
          "image_s3_key": None,
          "tags": [],
          "created_at": datetime(2024, 1, 1, tzinfo=timezone.utc),
          "embedding_status": "pending",
      }
      with patch("app.main.wardrobe_service") as mock_ws, \
           patch("app.main.auth.get_current_user_id", return_value="user-123"):
          mock_ws.create_wardrobe_item = AsyncMock(return_value=item_dict)
          resp = client.post(
              "/wardrobe",
              data={"name": "Test Blazer"},
              headers={"Authorization": f"Bearer {valid_jwt_token}"},
          )
      assert resp.status_code == 201
      assert "embedding_status" in resp.json()

  # ── GET /wardrobe/{item_id}/status ────────────────────────────────────────

  def test_get_wardrobe_item_status_returns_200(client, valid_jwt_token):
      from unittest.mock import patch, AsyncMock

      with patch("app.main.wardrobe_service") as mock_ws, \
           patch("app.main.auth.get_current_user_id", return_value="user-123"):
          mock_ws.get_wardrobe_item_status = AsyncMock(return_value="done")
          resp = client.get(
              "/wardrobe/some-item-id/status",
              headers={"Authorization": f"Bearer {valid_jwt_token}"},
          )
      assert resp.status_code == 200
      body = resp.json()
      assert body["item_id"] == "some-item-id"
      assert body["embedding_status"] == "done"

  def test_get_wardrobe_item_status_returns_404_when_not_found(client, valid_jwt_token):
      from unittest.mock import patch, AsyncMock

      with patch("app.main.wardrobe_service") as mock_ws, \
           patch("app.main.auth.get_current_user_id", return_value="user-123"):
          mock_ws.get_wardrobe_item_status = AsyncMock(return_value=None)
          resp = client.get(
              "/wardrobe/nonexistent/status",
              headers={"Authorization": f"Bearer {valid_jwt_token}"},
          )
      assert resp.status_code == 404
  ```

- [ ] **Step 2: Run to confirm they fail**

  ```bash
  python -m pytest tests/test_api_endpoints.py -k "embedding_status or wardrobe_item_status" -v
  ```
  Expected: FAIL.

- [ ] **Step 3: Update `main.py` — 3 `WardrobeItemResponse` calls**

  In `app/main.py`, find all three `WardrobeItemResponse(...)` calls (in `list_wardrobe`, `add_wardrobe_item`, `update_wardrobe_item_endpoint`) and add `embedding_status=item["embedding_status"]` to each. Example:

  ```python
  WardrobeItemResponse(
      item_id=item["item_id"],
      name=item["name"],
      category=item["category"],
      image_url=image_url,
      tags=item["tags"],
      created_at=item["created_at"],
      embedding_status=item["embedding_status"],   # ← add this
  ).model_dump()
  ```

- [ ] **Step 4: Update `main.py` — replace inline `_embed_wardrobe_image` task**

  In `add_wardrobe_item`, remove the entire inner `async def _embed_wardrobe_image(...)` function and its `asyncio.create_task(...)` call. Replace with:

  ```python
  if image_s3_key:
      asyncio.create_task(
          wardrobe_service.embed_wardrobe_item(item["item_id"], image_s3_key)
      )
  ```

  (`wardrobe_service` is already imported via `from app.services import wardrobe_service` inside the function body.)

- [ ] **Step 5: Add `GET /wardrobe/{item_id}/status` endpoint to `main.py`**

  Add after the existing `DELETE /wardrobe/{item_id}` endpoint:

  ```python
  @app.get("/wardrobe/{item_id}/status")
  async def get_wardrobe_item_status(
      item_id: str,
      request: Request,
      user_id: str = Depends(auth.get_current_user_id),
  ) -> WardrobeItemStatusResponse:
      """
      Return embedding_status for a wardrobe item.

      When called from HTMX (HX-Request header present), returns an HTML badge
      partial instead of JSON. Polling stops automatically when the badge for
      terminal states (done/failed) omits hx-trigger.
      """
      from app.services import wardrobe_service
      from app.models.wardrobe import WardrobeItemStatusResponse
      from fastapi.responses import HTMLResponse

      status = await wardrobe_service.get_wardrobe_item_status(user_id, item_id)
      if status is None:
          raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Wardrobe item not found")

      if request.headers.get("HX-Request"):
          if status in ("pending", "embedding"):
              html = (
                  f'<span id="embed-status-{item_id}" '
                  f'hx-get="/wardrobe/{item_id}/status" '
                  f'hx-trigger="every 2s" hx-target="this" hx-swap="outerHTML" '
                  f'class="badge badge-pending">⏳ {status}</span>'
              )
          elif status == "done":
              html = f'<span id="embed-status-{item_id}" class="badge badge-done">✓ ready</span>'
          else:
              html = f'<span id="embed-status-{item_id}" class="badge badge-failed">✗ failed</span>'
          return HTMLResponse(content=html)

      return WardrobeItemStatusResponse(item_id=item_id, embedding_status=status)
  ```

  Note: `from fastapi import Request` must be imported at the top of `main.py` — check if it's already there; add it to the existing FastAPI imports if not.

  Also fix the 404 raise — `status` is a string at that point; use `status_code=404`:
  ```python
  raise HTTPException(status_code=404, detail="Wardrobe item not found")
  ```

- [ ] **Step 6: Run tests to confirm they pass**

  ```bash
  python -m pytest tests/test_api_endpoints.py -k "embedding_status or wardrobe_item_status" -v
  ```
  Expected: PASS.

- [ ] **Step 7: Run full test suite**

  ```bash
  make test 2>&1 | tail -20
  ```
  Expected: all tests pass.

- [ ] **Step 8: Commit**

  ```bash
  git add app/main.py tests/test_api_endpoints.py
  git commit -m "feat: add embedding_status to wardrobe routes and GET /wardrobe/{item_id}/status endpoint"
  ```

---

## Task 8: Update frontend `wardrobe_card`

**Files:**
- Modify: `frontend/app.py`

The `wardrobe_card` function at line ~1113 renders a single wardrobe item. It needs an embedding status badge. No new route is needed — the existing `GET /wardrobe/{item_id}/status` handles the HTMX badge partial.

- [ ] **Step 1: Update `wardrobe_card` in `frontend/app.py`**

  Locate `def wardrobe_card(item: dict) -> Div:` and add the status badge inside the returned `Div`. The badge should appear between the thumbnail and the name:

  ```python
  def wardrobe_card(item: dict) -> Div:
      """A single wardrobe item card with thumbnail and delete button."""
      item_id = item["item_id"]
      image_url = item.get("image_url")
      embedding_status = item.get("embedding_status", "pending")

      thumbnail = (
          Img(src=image_url, alt=item["name"])
          if image_url
          else Div("👔", cls="wardrobe-card-placeholder")
      )

      # Build the status badge
      if embedding_status in ("pending", "embedding"):
          status_badge = Span(
              f"⏳ {embedding_status}",
              id=f"embed-status-{item_id}",
              cls="badge badge-pending",
              hx_get=f"/wardrobe/{item_id}/status",
              hx_trigger="every 2s",
              hx_target="this",
              hx_swap="outerHTML",
          )
      elif embedding_status == "done":
          status_badge = Span("✓ ready", id=f"embed-status-{item_id}", cls="badge badge-done")
      else:
          status_badge = Span("✗ failed", id=f"embed-status-{item_id}", cls="badge badge-failed")

      return Div(
          thumbnail,
          status_badge,
          Div(item["name"], cls="wardrobe-card-name"),
          Div(item.get("category") or "—", cls="wardrobe-card-category"),
          Button(
              "Delete",
              hx_delete=f"/wardrobe/{item_id}",
              hx_confirm="Remove this item from your wardrobe?",
              hx_target="closest .wardrobe-card",
              hx_swap="outerHTML swap:0.2s",
              cls="wardrobe-card-delete",
          ),
          cls="wardrobe-card",
          id=f"wardrobe-card-{item_id}",
      )
  ```

- [ ] **Step 2: Add badge CSS to the stylesheet in `frontend/app.py`**

  Find the `/* Wardrobe Styles */` section and add:

  ```css
  .badge {
      display: inline-block;
      font-size: 0.7rem;
      padding: 0.15rem 0.4rem;
      border-radius: 3px;
      margin-bottom: 0.25rem;
      font-weight: bold;
  }
  .badge-pending { background: #fef9c3; color: #854d0e; border: 1px solid #fde047; }
  .badge-done    { background: #dcfce7; color: #166534; border: 1px solid #86efac; }
  .badge-failed  { background: #fee2e2; color: #991b1b; border: 1px solid #fca5a5; }
  ```

- [ ] **Step 3: Smoke test the frontend manually**

  Start both servers (see CLAUDE.md for commands) and upload a wardrobe item with an image. Verify the badge shows "⏳ pending" immediately, and (if the embedding server is reachable) transitions to "✓ ready" within a few seconds.

- [ ] **Step 4: Run frontend tests**

  ```bash
  python -m pytest tests/test_frontend.py -v 2>&1 | tail -20
  ```
  Expected: all pass (the frontend test likely mocks the backend; confirm no new failures).

- [ ] **Step 5: Commit**

  ```bash
  git add frontend/app.py
  git commit -m "feat: add embedding status badge with HTMX polling to wardrobe card"
  ```

---

## Task 9: Final verification

- [ ] **Step 1: Run complete test suite**

  ```bash
  make test 2>&1 | tail -30
  ```
  Expected: all tests pass, 0 failures.

- [ ] **Step 2: Verify no regressions in wardrobe endpoints**

  Start the backend locally (`uvicorn app.main:app --host 127.0.0.1 --port 8000`) and confirm:
  - `GET /wardrobe` returns items with `embedding_status` field
  - `POST /wardrobe` returns `embedding_status: "pending"` in the response
  - `GET /wardrobe/{item_id}/status` returns JSON with `item_id` and `embedding_status`
  - `GET /wardrobe/{item_id}/status` with `HX-Request: true` header returns HTML badge

- [ ] **Step 3: Note for deployment**

  The `db_migrate.py` script must be run on the RDS instance before deploying the updated backend. The migration is safe to run on a live table (uses `IF NOT EXISTS`, has no data migration). Run via:

  ```bash
  make tunnel  # open SSH tunnel to RDS
  python scripts/db_migrate.py
  ```
