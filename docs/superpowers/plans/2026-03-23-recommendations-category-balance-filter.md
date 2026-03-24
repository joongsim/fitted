# Recommendations: Category Balance & Filter — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add category balancing to unfiltered recommendations and a per-category filter UI that re-fetches from the backend.

**Architecture:** Three layers of change: (1) `dev_catalog_service.search()` gains a `category_filter` SQL param; (2) `RecommendationService.recommend()` gains `_balance_by_category` and cache-bypass logic; (3) the frontend `get_recommendations` handler gains a `category` param and renders a persistent filter bar inside every `#rec-results` swap.

**Tech Stack:** FastAPI + Pydantic v2 (backend), FastHTML + HTMX (frontend), psycopg3 async (DB), pytest with `asyncio_mode=auto` (tests).

---

## File Map

| File | Change |
|------|--------|
| `app/main.py` | Add `category_filter` to `RecommendRequest`; pass it to `service.recommend()` |
| `app/services/dev_catalog_service.py` | Add `category_filter` param + conditional SQL `ILIKE` clause |
| `app/services/recommendation_service.py` | Add `_balance_by_category`; defer `[:top_k]` slice; add cache-bypass logic |
| `frontend/app.py` | Add `filter_bar()` helper; update `get_recommendations` signature + all response branches |
| `tests/test_dev_catalog_service.py` | New tests for category_filter SQL behaviour |
| `tests/test_recommendation_service.py` | New tests for `_balance_by_category` and filtered/unfiltered `recommend()` |

---

## Task 1: `dev_catalog_service.search()` — category filter SQL

**Files:**
- Modify: `app/services/dev_catalog_service.py`
- Test: `tests/test_dev_catalog_service.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_dev_catalog_service.py`:

```python
class TestDevCatalogCategoryFilter:
    async def test_category_filter_adds_ilike_to_primary_query(self):
        """When category_filter is set, SQL must contain ILIKE."""
        mock_conn, mock_cur = _make_mock_conn(fetchall_return=[_catalog_row()])

        with _patch_conn(mock_conn):
            from app.services.dev_catalog_service import search
            await search(_UNIT_VEC, category_filter="tops")

        sql, params = mock_cur.execute.call_args_list[0][0]
        assert "ilike" in sql.lower()
        assert "%tops%" in params

    async def test_category_filter_pattern_is_wrapped_in_percent(self):
        """Bound parameter must be '%<value>%', not just '<value>'."""
        mock_conn, mock_cur = _make_mock_conn(fetchall_return=[_catalog_row()])

        with _patch_conn(mock_conn):
            from app.services.dev_catalog_service import search
            await search(_UNIT_VEC, category_filter="shoes")

        _, params = mock_cur.execute.call_args_list[0][0]
        assert "%shoes%" in params

    async def test_no_category_filter_omits_ilike(self):
        """Without category_filter, SQL must not contain ILIKE."""
        mock_conn, mock_cur = _make_mock_conn(fetchall_return=[_catalog_row()])

        with _patch_conn(mock_conn):
            from app.services.dev_catalog_service import search
            await search(_UNIT_VEC)

        sql, _ = mock_cur.execute.call_args_list[0][0]
        assert "ilike" not in sql.lower()

    async def test_category_filter_applied_to_fallback_query(self):
        """Fallback recency query must also carry the ILIKE filter."""
        mock_conn, mock_cur = _make_mock_conn()
        fallback_row = _catalog_row(item_id="fb", cosine_distance=None)
        mock_cur.fetchall = AsyncMock(side_effect=[[], [fallback_row]])

        with _patch_conn(mock_conn):
            from app.services.dev_catalog_service import search
            await search(_UNIT_VEC, category_filter="bottoms")

        assert mock_cur.execute.call_count == 2
        fallback_sql, fallback_params = mock_cur.execute.call_args_list[1][0]
        assert "ilike" in fallback_sql.lower()
        assert "%bottoms%" in fallback_params

    async def test_primary_query_param_order_with_filter(self):
        """Primary query params must be (embedding, domain, pattern, limit)."""
        mock_conn, mock_cur = _make_mock_conn(fetchall_return=[_catalog_row()])

        with _patch_conn(mock_conn):
            from app.services.dev_catalog_service import search
            await search(_UNIT_VEC, domain="fashion", limit=50, category_filter="tops")

        _, params = mock_cur.execute.call_args_list[0][0]
        # params[0] = embedding list, params[1] = domain, params[2] = pattern, params[3] = limit
        assert isinstance(params[0], list)
        assert params[1] == "fashion"
        assert params[2] == "%tops%"
        assert params[3] == 50
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd C:/Projects/fitted && python -m pytest tests/test_dev_catalog_service.py::TestDevCatalogCategoryFilter -v
```

Expected: FAIL — `search()` does not accept `category_filter` yet.

- [ ] **Step 3: Implement `category_filter` in `dev_catalog_service.search()`**

In `app/services/dev_catalog_service.py`, update the `search` signature and SQL:

```python
async def search(
    query_embedding: np.ndarray,
    limit: int = 50,
    domain: str = "fashion",
    category_filter: Optional[str] = None,
) -> list[Item]:
```

Add `from typing import Optional` at the top if not already present.

Replace the primary query execution block:

```python
    if category_filter:
        await cur.execute(
            """
            SELECT item_id, domain, title, price, image_url, product_url,
                   source, embedding, attributes,
                   (embedding <=> %s::vector) AS cosine_distance
            FROM catalog_items
            WHERE domain = %s
              AND embedding IS NOT NULL
              AND attributes->>'category' ILIKE %s
            ORDER BY cosine_distance
            LIMIT %s
            """,
            (embedding_list, domain, f"%{category_filter}%", limit),
        )
    else:
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
```

Replace the fallback query execution block:

```python
            if category_filter:
                await cur.execute(
                    """
                    SELECT item_id, domain, title, price, image_url, product_url,
                           source, embedding, attributes, NULL AS cosine_distance
                    FROM catalog_items
                    WHERE domain = %s
                      AND attributes->>'category' ILIKE %s
                    ORDER BY last_seen DESC
                    LIMIT %s
                    """,
                    (domain, f"%{category_filter}%", limit),
                )
            else:
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_dev_catalog_service.py -v
```

Expected: All tests PASS (new + existing).

- [ ] **Step 5: Commit**

```bash
git add app/services/dev_catalog_service.py tests/test_dev_catalog_service.py
git commit -m "feat: add category_filter param to dev_catalog_service.search"
```

---

## Task 2: `_balance_by_category` helper

**Files:**
- Modify: `app/services/recommendation_service.py`
- Test: `tests/test_recommendation_service.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_recommendation_service.py`:

```python
from app.services.recommendation_service import _balance_by_category


def _make_categorized_item(item_id: str, category: str, score: float):
    item = Item(
        item_id=item_id,
        domain="fashion",
        title=f"Item {item_id}",
        price=10.0,
        image_url="",
        product_url="",
        source="poshmark_seed",
        embedding=None,
        attributes={"category": category},
    )
    return (item, score)


class TestBalanceByCategory:
    def test_returns_top_k_items(self):
        ranked = [
            _make_categorized_item(f"t{i}", "tops", 1.0 - i * 0.01) for i in range(5)
        ] + [
            _make_categorized_item(f"b{i}", "bottoms", 0.9 - i * 0.01) for i in range(5)
        ]
        result = _balance_by_category(ranked, top_k=4)
        assert len(result) == 4

    def test_alternates_categories_round_robin(self):
        ranked = [
            _make_categorized_item("t1", "tops", 1.0),
            _make_categorized_item("t2", "tops", 0.9),
            _make_categorized_item("b1", "bottoms", 0.8),
            _make_categorized_item("b2", "bottoms", 0.7),
        ]
        result = _balance_by_category(ranked, top_k=4)
        categories = [item.attributes["category"] for item, _ in result]
        # Should alternate: tops, bottoms, tops, bottoms
        assert categories[0] != categories[1]
        assert categories[0] == categories[2]
        assert categories[1] == categories[3]

    def test_handles_fewer_candidates_than_top_k(self):
        ranked = [_make_categorized_item("t1", "tops", 1.0)]
        result = _balance_by_category(ranked, top_k=10)
        assert len(result) == 1

    def test_skips_exhausted_category_and_continues(self):
        ranked = [
            _make_categorized_item("t1", "tops", 1.0),
            _make_categorized_item("b1", "bottoms", 0.9),
            _make_categorized_item("b2", "bottoms", 0.8),
            _make_categorized_item("b3", "bottoms", 0.7),
        ]
        result = _balance_by_category(ranked, top_k=4)
        ids = [item.item_id for item, _ in result]
        assert "t1" in ids
        # After tops exhausted, remaining slots should be filled from bottoms
        assert len(result) == 4

    def test_items_missing_category_grouped_under_other(self):
        ranked = [
            _make_categorized_item("x1", "tops", 1.0),
            (
                Item(
                    item_id="no-cat",
                    domain="fashion",
                    title="No cat",
                    price=5.0,
                    image_url="",
                    product_url="",
                    source="poshmark_seed",
                    embedding=None,
                    attributes={},
                ),
                0.5,
            ),
        ]
        result = _balance_by_category(ranked, top_k=2)
        assert len(result) == 2

    def test_preserves_score_order_within_category(self):
        ranked = [
            _make_categorized_item("t1", "tops", 1.0),
            _make_categorized_item("t2", "tops", 0.5),
            _make_categorized_item("b1", "bottoms", 0.8),
        ]
        result = _balance_by_category(ranked, top_k=3)
        tops_in_result = [(item, score) for item, score in result if item.attributes.get("category") == "tops"]
        if len(tops_in_result) == 2:
            assert tops_in_result[0][1] > tops_in_result[1][1]

    def test_empty_input_returns_empty(self):
        assert _balance_by_category([], top_k=5) == []
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_recommendation_service.py::TestBalanceByCategory -v
```

Expected: FAIL — `_balance_by_category` not defined.

- [ ] **Step 3: Implement `_balance_by_category`**

Add this function to `app/services/recommendation_service.py` (before the `RecommendationService` class):

```python
def _balance_by_category(
    ranked: list[tuple["Item", float]],
    top_k: int,
) -> list[tuple["Item", float]]:
    """
    Return up to top_k items with a best-effort balanced mix of categories.

    Algorithm: group by category, then round-robin across groups picking the
    highest-scored item from each until top_k is reached or all groups are
    exhausted. Items with no category attribute are grouped under 'other'.
    """
    from collections import defaultdict

    groups: dict[str, list[tuple["Item", float]]] = defaultdict(list)
    for item, score in ranked:
        cat = item.attributes.get("category", "other")
        groups[cat].append((item, score))

    # groups are already in descending score order (ranked is pre-sorted)
    group_iters = {cat: iter(items) for cat, items in groups.items()}
    active_cats = list(group_iters.keys())

    result: list[tuple["Item", float]] = []
    while len(result) < top_k and active_cats:
        exhausted = []
        for cat in active_cats:
            if len(result) >= top_k:
                break
            try:
                result.append(next(group_iters[cat]))
            except StopIteration:
                exhausted.append(cat)
        for cat in exhausted:
            active_cats.remove(cat)

    return result
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_recommendation_service.py::TestBalanceByCategory -v
```

Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/recommendation_service.py tests/test_recommendation_service.py
git commit -m "feat: add _balance_by_category helper to recommendation_service"
```

---

## Task 3: Wire `category_filter` into `recommend()` and update `RecommendRequest`

**Files:**
- Modify: `app/services/recommendation_service.py`
- Modify: `app/main.py`
- Test: `tests/test_recommendation_service.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_recommendation_service.py` inside `TestRecommend`:

```python
    async def test_filtered_request_skips_cache_lookup(self):
        """When category_filter is set, cache lookup must not be called."""
        svc = self._svc()
        candidates = [_make_item("i1", embedding=_UNIT_VEC.copy())]
        mock_conn, _ = _make_mock_conn(fetchall_return=[])
        mock_cache_lookup = AsyncMock(return_value=None)

        with ExitStack() as stack:
            stack.enter_context(patch(_PATCH_LLM, new=AsyncMock(return_value="query")))
            stack.enter_context(patch(_PATCH_ENCODE, return_value=_UNIT_VEC.copy()))
            stack.enter_context(patch(_PATCH_CACHE_LOOKUP, new=mock_cache_lookup))
            stack.enter_context(patch(_PATCH_CATALOG, new=AsyncMock(return_value=candidates)))
            stack.enter_context(patch(_PATCH_CACHE_STORE, new=AsyncMock()))
            stack.enter_context(patch(_PATCH_CONN, return_value=_mock_get_connection(mock_conn)))
            stack.enter_context(patch(_PATCH_PREF_SCORES, new=AsyncMock(return_value={})))
            await svc.recommend(
                user_id="u1",
                location="London",
                weather_context={"temp_c": 18.0, "condition": "Sunny"},
                style_preferences={},
                category_filter="tops",
            )

        mock_cache_lookup.assert_not_called()

    async def test_filtered_request_skips_cache_store(self):
        """When category_filter is set, cache store must not be called."""
        svc = self._svc()
        candidates = [_make_item("i1", embedding=_UNIT_VEC.copy())]
        mock_conn, _ = _make_mock_conn(fetchall_return=[])
        mock_cache_store = AsyncMock()

        with ExitStack() as stack:
            stack.enter_context(patch(_PATCH_LLM, new=AsyncMock(return_value="query")))
            stack.enter_context(patch(_PATCH_ENCODE, return_value=_UNIT_VEC.copy()))
            stack.enter_context(patch(_PATCH_CACHE_LOOKUP, new=AsyncMock(return_value=None)))
            stack.enter_context(patch(_PATCH_CATALOG, new=AsyncMock(return_value=candidates)))
            stack.enter_context(patch(_PATCH_CACHE_STORE, new=mock_cache_store))
            stack.enter_context(patch(_PATCH_CONN, return_value=_mock_get_connection(mock_conn)))
            stack.enter_context(patch(_PATCH_PREF_SCORES, new=AsyncMock(return_value={})))
            await svc.recommend(
                user_id="u1",
                location="London",
                weather_context={"temp_c": 18.0, "condition": "Sunny"},
                style_preferences={},
                category_filter="tops",
            )

        mock_cache_store.assert_not_called()

    async def test_filtered_request_passes_category_to_catalog(self):
        """category_filter must be forwarded to dev_catalog_service.search()."""
        svc = self._svc()
        mock_conn, _ = _make_mock_conn(fetchall_return=[])
        mock_catalog = AsyncMock(return_value=[_make_item("i1", embedding=_UNIT_VEC.copy())])

        with ExitStack() as stack:
            stack.enter_context(patch(_PATCH_LLM, new=AsyncMock(return_value="query")))
            stack.enter_context(patch(_PATCH_ENCODE, return_value=_UNIT_VEC.copy()))
            stack.enter_context(patch(_PATCH_CACHE_LOOKUP, new=AsyncMock(return_value=None)))
            stack.enter_context(patch(_PATCH_CATALOG, new=mock_catalog))
            stack.enter_context(patch(_PATCH_CACHE_STORE, new=AsyncMock()))
            stack.enter_context(patch(_PATCH_CONN, return_value=_mock_get_connection(mock_conn)))
            stack.enter_context(patch(_PATCH_PREF_SCORES, new=AsyncMock(return_value={})))
            await svc.recommend(
                user_id="u1",
                location="London",
                weather_context={"temp_c": 18.0, "condition": "Sunny"},
                style_preferences={},
                category_filter="shoes",
            )

        call_kwargs = mock_catalog.call_args.kwargs
        assert call_kwargs.get("category_filter") == "shoes"

    async def test_unfiltered_request_applies_balance(self):
        """Without category_filter, result must contain items from multiple categories."""
        svc = self._svc()
        # 10 tops then 10 bottoms — balancer should interleave them
        candidates = (
            [_make_item_with_category(f"t{i}", "tops") for i in range(10)]
            + [_make_item_with_category(f"b{i}", "bottoms") for i in range(10)]
        )
        mock_conn, _ = _make_mock_conn(fetchall_return=[])

        with ExitStack() as stack:
            stack.enter_context(patch(_PATCH_LLM, new=AsyncMock(return_value="query")))
            stack.enter_context(patch(_PATCH_ENCODE, return_value=_UNIT_VEC.copy()))
            stack.enter_context(patch(_PATCH_CACHE_LOOKUP, new=AsyncMock(return_value=None)))
            stack.enter_context(patch(_PATCH_CATALOG, new=AsyncMock(return_value=candidates)))
            stack.enter_context(patch(_PATCH_CACHE_STORE, new=AsyncMock(return_value="id")))
            stack.enter_context(patch(_PATCH_CONN, return_value=_mock_get_connection(mock_conn)))
            stack.enter_context(patch(_PATCH_PREF_SCORES, new=AsyncMock(return_value={})))
            result = await svc.recommend(
                user_id="u1",
                location="London",
                weather_context={"temp_c": 18.0, "condition": "Sunny"},
                style_preferences={},
                top_k=4,
            )

        cats = {r.attributes.get("category") for r in result}
        assert len(cats) > 1  # both tops and bottoms should appear
```

Also add the helper at module level in the test file:

```python
def _make_item_with_category(item_id: str, category: str) -> Item:
    return Item(
        item_id=item_id,
        domain="fashion",
        title=f"Item {item_id}",
        price=10.0,
        image_url="",
        product_url="",
        source="poshmark_seed",
        embedding=_UNIT_VEC.copy(),
        attributes={"category": category},
    )
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_recommendation_service.py::TestRecommend::test_filtered_request_skips_cache_lookup tests/test_recommendation_service.py::TestRecommend::test_filtered_request_skips_cache_store tests/test_recommendation_service.py::TestRecommend::test_filtered_request_passes_category_to_catalog tests/test_recommendation_service.py::TestRecommend::test_unfiltered_request_applies_balance -v
```

Expected: FAIL — `recommend()` doesn't accept `category_filter` yet.

- [ ] **Step 3: Update `RecommendationService.recommend()` signature and logic**

In `app/services/recommendation_service.py`, update the `recommend` method:

1. Add `category_filter: Optional[str] = None` to the signature:

```python
    async def recommend(
        self,
        user_id: str,
        location: str,
        weather_context: dict,
        style_preferences: dict,
        top_k: int = 10,
        include_explanation: bool = False,
        category_filter: Optional[str] = None,
    ) -> list[ProductRecommendation]:
```

2. Replace the cache lookup/store block (steps 3–5) with:

```python
        # Step 3: Check the vector cache (skip entirely when category_filter is set)
        if category_filter:
            candidates = await dev_catalog_service.search(
                query_embedding=query_embedding,
                limit=50,
                category_filter=category_filter,
            )
            if not candidates:
                logger.warning(
                    "No candidates found for user_id=%s category_filter=%s — returning empty list",
                    user_id,
                    category_filter,
                )
                return []
        else:
            cache_result = await vector_cache.lookup(query_embedding)
            if cache_result is not None:
                candidates, _ = cache_result
                logger.info("Using cached candidates: count=%d", len(candidates))
            else:
                candidates = await dev_catalog_service.search(
                    query_embedding=query_embedding,
                    limit=50,
                )
                if not candidates:
                    logger.warning(
                        "No candidates found for user_id=%s — skipping cache store",
                        user_id,
                    )
                    return []
                await vector_cache.store(
                    query_text=query_text,
                    query_embedding=query_embedding,
                    items=candidates,
                    s3_client=self._s3_client,
                    bucket=self._bucket,
                )
```

3. Remove the now-redundant `if not candidates:` guard that follows (it's subsumed above).

4. Replace the preference reranking + slice block:

```python
        # Step 7.5: Preference reranking (no-op when user has no preference pairs)
        from app.services import preference_reranker

        pref_scores = await preference_reranker.get_preference_scores(user_id)
        ranked = preference_reranker.rerank(ranked, pref_scores)  # no slice here

        if category_filter:
            ranked = ranked[:top_k]
        else:
            ranked = _balance_by_category(ranked, top_k)
```

- [ ] **Step 4: Update `RecommendRequest` in `app/main.py`**

Add the import and field. Find `class RecommendRequest(BaseModel):` (around line 65) and update:

```python
from typing import Literal, Optional

CATEGORY_FILTER_VALUES = Literal["tops", "bottoms", "shoes", "outerwear", "accessories"]

class RecommendRequest(BaseModel):
    location: Annotated[
        str, StringConstraints(min_length=1, max_length=200, strip_whitespace=True)
    ]
    include_explanation: bool = False
    category_filter: Optional[CATEGORY_FILTER_VALUES] = None
```

Note: `Optional` and `Literal` may already be imported — check the existing imports at the top of `app/main.py` and add only what's missing.

- [ ] **Step 5: Pass `category_filter` through the route**

Find the `service.recommend(...)` call (around line 797) and add the new kwarg:

```python
        recommendations = await service.recommend(
            user_id=user_id,
            location=request.location,
            weather_context=weather_context,
            style_preferences=style_preferences,
            top_k=10,
            include_explanation=request.include_explanation,
            category_filter=request.category_filter,
        )
```

- [ ] **Step 6: Run all backend tests**

```bash
python -m pytest tests/test_recommendation_service.py tests/test_dev_catalog_service.py -v
```

Expected: All PASS.

- [ ] **Step 7: Commit**

```bash
git add app/services/recommendation_service.py app/main.py tests/test_recommendation_service.py
git commit -m "feat: wire category_filter through recommend() pipeline with cache bypass and balancing"
```

---

## Task 4: Frontend — `filter_bar` helper and updated `get_recommendations`

**Files:**
- Modify: `frontend/app.py`

- [ ] **Step 1: Add the `filter_bar` helper**

In `frontend/app.py`, add this helper near the other helper functions (before the route definitions). The `FT` type is already used in the file — no new import needed.

```python
_CATEGORIES = [
    ("all", "All"),
    ("tops", "Tops"),
    ("bottoms", "Bottoms"),
    ("shoes", "Shoes"),
    ("outerwear", "Outerwear"),
    ("accessories", "Accessories"),
]


def filter_bar(location: str, active: str):
    """Filter pill row — included in every #rec-results swap."""
    buttons = []
    for value, label in _CATEGORIES:
        is_active = value == active
        buttons.append(
            Form(
                Input(type="hidden", name="location", value=location),
                Input(type="hidden", name="category", value=value),
                Button(
                    label,
                    type="submit",
                    cls="filter-btn active" if is_active else "filter-btn",
                ),
                hx_post="/get-recommendations",
                hx_target="#rec-results",
                hx_swap="outerHTML",
                style="display:inline",
            )
        )
    return Div(*buttons, cls="filter-bar")
```

- [ ] **Step 2: Update `get_recommendations` handler signature**

Find the `async def get_recommendations(location: str, session):` handler (around line 1392) and update:

```python
@app.post("/get-recommendations")
async def get_recommendations(location: str, session, category: str = "all"):
    """HTMX fragment: call POST /recommend-products, return a product card grid."""
```

- [ ] **Step 3: Update the backend payload**

Find the line `json={"location": location, "include_explanation": False}` and replace with:

```python
                json={
                    "location": location,
                    "include_explanation": False,
                    "category_filter": None if category == "all" else category,
                },
```

- [ ] **Step 4: Update all response branches to include `filter_bar`**

**Auth error** (early return, no location yet — leave as-is, no filter bar):
```python
    if "access_token" not in session:
        return P("Please log in to get recommendations.", cls="error-message")
```

**Missing location** (early return, no filter bar):
```python
    if not location or not location.strip():
        return P("Please enter a location.", cls="error-message")
```

**Backend error response** (non-200) — find the `return P(f"Error: {err}", ...)` and replace:

```python
                return Div(
                    filter_bar(location, category),
                    P(f"Error: {err}", cls="error-message"),
                    id="rec-results",
                )
```

**Connection error response** — find the `return P("Connection error: ...")` and replace:

```python
        return Div(
            filter_bar(location, category),
            P("Connection error: could not reach the server.", cls="error-message"),
            id="rec-results",
        )
```

**Empty results** — find `return P("No recommendations found...", ...)` and replace:

```python
    if not recommendations:
        return Div(
            filter_bar(location, category),
            P("No recommendations found for this location.", cls="rec-meta"),
            id="rec-results",
        )
```

**Success response** — find the final `return Div(P(...), Div(*[product_card(...)...]), id="rec-results")` and replace:

```python
    return Div(
        filter_bar(location, category),
        P(
            f"Top picks for {location} ({condition}, {temp_c:.0f}\u00b0C)",
            cls="rec-meta",
        ),
        Div(*[product_card(item) for item in recommendations], cls="product-grid"),
        id="rec-results",
    )
```

- [ ] **Step 5: Add filter bar CSS**

Find the CSS block in `frontend/app.py` (look for the `style` string or inline styles). Add:

```css
.filter-bar { display: flex; flex-wrap: wrap; gap: 0.5rem; margin-bottom: 1rem; }
.filter-btn { padding: 0.35rem 0.85rem; border-radius: 9999px; border: 1px solid #cbd5e1; background: #f8fafc; color: #334155; cursor: pointer; font-size: 0.85rem; }
.filter-btn.active { background: #1e293b; color: #f8fafc; border-color: #1e293b; }
.filter-btn:hover:not(.active) { background: #e2e8f0; }
```

- [ ] **Step 6: Run the full test suite**

```bash
python -m pytest tests/ -v
```

Expected: All PASS.

- [ ] **Step 7: Commit**

```bash
git add frontend/app.py
git commit -m "feat: add category filter bar to recommendations page"
```

---

## Task 5: Smoke test end-to-end

- [ ] **Step 1: Start backend**

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

- [ ] **Step 2: Start frontend**

```bash
cd frontend && uvicorn app:app --host 127.0.0.1 --port 5001
```

- [ ] **Step 3: Manual checks**

1. Navigate to `/recommendations`, enter a location, submit. Verify filter bar appears with "All" active.
2. Click "Tops". Verify page re-fetches and "Tops" is highlighted.
3. Click "All". Verify "All" is highlighted and results appear.
4. Click a category with no results (if applicable). Verify filter bar still appears with correct active state.
5. Check backend logs: filtered requests should show no `"Using cached candidates"` log line.
