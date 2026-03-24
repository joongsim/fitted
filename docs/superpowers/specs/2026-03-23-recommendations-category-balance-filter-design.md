# Recommendations: Category Balance & Filter

**Date:** 2026-03-23
**Status:** Approved

## Summary

Two features for the `/recommend-products` endpoint and the `/recommendations` frontend page:

1. **Category balance** — when returning unfiltered results, the top-k list is diversified across item categories (tops, bottoms, shoes, outerwear, accessories) using a best-effort round-robin selection.
2. **Category filter** — users can filter recommendations to a single category; filtered requests bypass the vector cache and run a category-aware ANN search.

## Backend

### `RecommendRequest` (app/main.py)

Add an optional `category_filter` field (project uses Pydantic v2):

```python
from typing import Literal, Optional
CATEGORY_FILTER_VALUES = Literal["tops", "bottoms", "shoes", "outerwear", "accessories"]

class RecommendRequest(BaseModel):
    location: ...
    include_explanation: bool = False
    category_filter: Optional[CATEGORY_FILTER_VALUES] = None
```

When the frontend sends `category_filter: null` in the JSON body, Pydantic maps it to `None`. Omitting the key entirely also resolves to `None`. Both are accepted.

### `dev_catalog_service.search()` (app/services/dev_catalog_service.py)

Add `category_filter: Optional[str] = None`. When set, append `AND attributes->>'category' ILIKE %s` to both the primary ANN query and the fallback recency query.

Pass the ILIKE pattern as a bound parameter: `f"%{category_filter}%"` (e.g. `"%tops%"`). The `%s` placeholder receives the full pattern string; psycopg3 handles the escaping. The `%` characters in the pattern are part of the value, not the placeholder syntax.

The `LIMIT` remains `50` for both filtered and unfiltered requests. The `ILIKE` filter is applied inside the SQL, so pgvector returns up to 50 items that match the category; there is no post-fetch filtering step.

**Updated parameter tuples:**

Primary query (with filter):
```python
(embedding_list, domain, f"%{category_filter}%", limit)
```

Fallback recency query (with filter):
```python
(domain, f"%{category_filter}%", limit)
```

When `category_filter is None`, the SQL and parameter tuples are unchanged from today.

> `dev_catalog_service.search()` accepts `category_filter: Optional[str]` without its own validation. Validation is enforced at the API boundary by the `Literal` type on `RecommendRequest`. `dev_catalog_service.search()` is only called from `RecommendationService.recommend()`, which is only reachable via the validated route.

### `RecommendationService.recommend()` (app/services/recommendation_service.py)

Add `category_filter: Optional[str] = None` parameter.

**Cache behaviour:**
- `category_filter is None` → use vector cache as today (lookup + store on miss)
- `category_filter is not None` → skip cache entirely (lookup and store both bypassed)

The LLM query generation (step 1) and CLIP embedding (step 2) **still run** for filtered requests — the query embedding is still needed as the ANN search vector. Only the cache lookup and store are skipped. The LLM query is driven by `style_preferences` and `weather_context` and is intentionally not category-aware; the category constraint is applied at the SQL layer, not in the prompt.

**Candidate fetch:** Pass `category_filter` to `dev_catalog_service.search()`.

**Balancing and top-k slice:**

The existing `[:top_k]` slice that currently happens inside the preference reranking call:

```python
ranked = preference_reranker.rerank(ranked, pref_scores)[:top_k]
```

must be deferred so that `_balance_by_category` receives the full reranked list. Change to:

```python
ranked = preference_reranker.rerank(ranked, pref_scores)  # no slice here

if category_filter:
    ranked = ranked[:top_k]
else:
    ranked = _balance_by_category(ranked, top_k)
```

`_balance_by_category` applies the `top_k` limit internally.

**`_balance_by_category` signature and logic:**

```python
def _balance_by_category(
    ranked: list[tuple[Item, float]],
    top_k: int,
) -> list[tuple[Item, float]]:
```

`top_k` is the same value passed to `recommend()` (currently `10` at the call site in `main.py`).

Algorithm:
1. Group `(item, score)` pairs by `item.attributes.get("category", "other")`, preserving descending score order within each group.
2. Round-robin across groups, popping the highest-scored item from each group per pass.
3. Skip exhausted groups.
4. Stop when `top_k` items are collected.

If the total number of candidates is less than `top_k` (e.g. sparse catalog), the function returns all candidates without error. Returning fewer than `top_k` items is acceptable.

### Route (app/main.py `/recommend-products`)

Pass `request.category_filter` to `service.recommend()`.

## Frontend

### Filter bar

A row of pill/button filters rendered as part of the `#rec-results` fragment:

```
[ All ]  [ Tops ]  [ Bottoms ]  [ Shoes ]  [ Outerwear ]  [ Accessories ]
```

Active category is visually highlighted. Implemented as a helper:

```python
def filter_bar(location: str, active: str) -> FT:
    ...
```

Each filter is a `Form` (or `Button` inside a `Form`) that POSTs to `/get-recommendations` with:
- A hidden `Input(type="hidden", name="location", value=location)` carrying the current location string
- A hidden `Input(type="hidden", name="category", value=cat_value)`
- `hx_post="/get-recommendations"`, `hx_target="#rec-results"`, `hx_swap="outerHTML"`

The hidden `location` input ensures the location is preserved across filter taps without requiring it to appear in the URL or session.

### `/recommendations` page (frontend/app.py)

Initial page render is unchanged — no filter bar shown until results are loaded (filter bar appears inside the swapped `#rec-results` fragment).

### `/get-recommendations` handler (frontend/app.py)

Signature:

```python
async def get_recommendations(location: str, session, category: str = "all"):
```

FastHTML extracts both `location` and `category` from the POST form body.

Backend JSON payload:
- `category == "all"` → `{"location": location, "include_explanation": False, "category_filter": None}`
- Otherwise → `{"location": location, "include_explanation": False, "category_filter": category}`

**Returned fragment** (replaces `#rec-results`):

```
Div(
  filter_bar(location, category),         # filter buttons, location hidden input, active state
  P(weather meta),
  Div(*product_cards, cls="product-grid"),
  id="rec-results",
)
```

The filter bar is included in **every** response — including empty-result and error responses — so the user can always switch categories without reloading the page.

**Empty result response** (when `recommendations` is empty):

```
Div(
  filter_bar(location, category),
  P("No recommendations found for this location.", cls="rec-meta"),
  id="rec-results",
)
```

**Error response** (backend non-200, connection error): include the filter bar when `location` is available; omit it only when `location` itself is missing (e.g. the initial missing-location validation error or unauthenticated path, both of which occur before `location` is known).

```
Div(
  filter_bar(location, category),   # only when location is known
  P(f"Error: {err}", cls="error-message"),
  id="rec-results",
)
```

On error, the active filter state is preserved (whichever category triggered the failed request). Do not reset to "All".

## Data

Fixed category list (case-insensitive match against `attributes->>'category'`):

| UI Label    | `category` form value | ILIKE bound parameter |
|-------------|----------------------|-----------------------|
| All         | `all`                | (no filter)           |
| Tops        | `tops`               | `%tops%`              |
| Bottoms     | `bottoms`            | `%bottoms%`           |
| Shoes       | `shoes`              | `%shoes%`             |
| Outerwear   | `outerwear`          | `%outerwear%`         |
| Accessories | `accessories`        | `%accessories%`       |

> Poshmark category strings vary (e.g. "Women's Tops", "Tops & Blouses"). `ILIKE '%tops%'` handles this without requiring an exhaustive enum.

## Error handling

- Invalid `category_filter` values are rejected by Pydantic at the route level (Literal type).
- If filtered ANN search returns no results, `recommend()` returns `[]` and the frontend shows the existing "no recommendations" message.

## What is not changing

- Vector cache TTL, key format, S3 storage — unchanged for unfiltered requests.
- Two-tower ranking, preference reranking logic — unchanged.
- LLM query generation — unchanged.
- Auth, weather fetch — unchanged.
