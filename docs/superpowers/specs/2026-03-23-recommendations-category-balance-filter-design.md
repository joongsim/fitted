# Recommendations: Category Balance & Filter

**Date:** 2026-03-23
**Status:** Approved

## Summary

Two features for the `/recommend-products` endpoint and the `/recommendations` frontend page:

1. **Category balance** — when returning unfiltered results, the top-k list is diversified across item categories (tops, bottoms, shoes, outerwear, accessories) using a best-effort round-robin selection.
2. **Category filter** — users can filter recommendations to a single category; filtered requests bypass the vector cache and run a category-aware ANN search.

## Backend

### `RecommendRequest` (app/main.py)

Add an optional `category_filter` field:

```python
from typing import Literal, Optional
CATEGORY_FILTER_VALUES = Literal["tops", "bottoms", "shoes", "outerwear", "accessories"]

class RecommendRequest(BaseModel):
    location: ...
    include_explanation: bool = False
    category_filter: Optional[CATEGORY_FILTER_VALUES] = None
```

### `dev_catalog_service.search()` (app/services/dev_catalog_service.py)

Add `category_filter: Optional[str] = None`. When set, appends `AND attributes->>'category' ILIKE %s` to the primary SQL query and the fallback recency query.

### `RecommendationService.recommend()` (app/services/recommendation_service.py)

Add `category_filter: Optional[str] = None` parameter.

**Cache behaviour:**
- `category_filter is None` → use vector cache as today (lookup + store on miss)
- `category_filter is not None` → skip cache entirely (lookup and store both bypassed)

**Candidate fetch:** Pass `category_filter` to `dev_catalog_service.search()`.

**Balancing:** Applied only when `category_filter is None`, after preference reranking, before returning results.

```
_balance_by_category(ranked: list[tuple[Item, float]], top_k: int) -> list[tuple[Item, float]]
```

1. Group `(item, score)` pairs by `item.attributes.get("category", "other")`, preserving descending score order within each group.
2. Round-robin across groups, popping the highest-scored item from each group per pass.
3. Skip exhausted groups.
4. Stop when `top_k` items are collected.

When `category_filter` is set, skip balancing and take `ranked[:top_k]` as today.

### Route (app/main.py `/recommend-products`)

Pass `request.category_filter` to `service.recommend()`.

## Frontend

### Filter bar

A row of pill/button filters rendered as part of the `#rec-results` fragment:

```
[ All ]  [ Tops ]  [ Bottoms ]  [ Shoes ]  [ Outerwear ]  [ Accessories ]
```

Active category is visually highlighted. Each button submits a form POST to `/get-recommendations` with both `location` (hidden input) and `category`.

### `/recommendations` page (frontend/app.py)

Initial page render is unchanged — no filter bar shown until results are loaded (filter bar appears inside the swapped `#rec-results` fragment).

### `/get-recommendations` handler (frontend/app.py)

Signature: `async def get_recommendations(location: str, session, category: str = "all")`

- `category == "all"` → send `category_filter=None` to backend
- Otherwise → send `category_filter=category` to backend

**Returned fragment** (replaces `#rec-results`):

```
Div(
  filter_bar(location, active_category),   # filter buttons with hidden location input
  P(weather meta),
  Div(*product_cards, cls="product-grid"),
  id="rec-results",
)
```

The filter bar is included in every response so the active state is always up to date after a swap.

## Data

Fixed category list (case-insensitive match against `attributes->>'category'`):

| UI Label   | Filter value | SQL ILIKE pattern |
|------------|-------------|-------------------|
| Tops       | tops        | `%tops%`          |
| Bottoms    | bottoms     | `%bottoms%`       |
| Shoes      | shoes       | `%shoes%`         |
| Outerwear  | outerwear   | `%outerwear%`     |
| Accessories| accessories | `%accessories%`   |

> Note: Poshmark category strings may vary (e.g. "Women's Tops", "Tops & Blouses"). `ILIKE '%tops%'` handles these without requiring an exhaustive enum.

## Error handling

- Invalid `category_filter` values are rejected by Pydantic at the route level (Literal type).
- If filtered ANN search returns no results, `recommend()` returns `[]` and the frontend shows the existing "no recommendations" message.

## What is not changing

- Vector cache TTL, key format, S3 storage — unchanged for unfiltered requests.
- Two-tower ranking, preference reranking — unchanged.
- LLM query generation — unchanged.
- Auth, weather fetch — unchanged.
