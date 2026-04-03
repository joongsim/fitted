# Serper Google Shopping Catalog Ingestion — Design Spec

**Date:** 2026-04-03
**Status:** Draft

## Overview

A one-off ingestion script that uses Serper's Google Shopping API to populate the `catalog_items` table with menswear products from specific brands. Follows the existing Poshmark ingestion pattern (standalone script, same DB schema, same S3 layout conventions).

## Brand Configuration

- File: `config/serper_brands.json`
- Format:
  ```json
  {
    "brands": [
      "Acne Studios",
      "AMI Paris",
      "Jacquemus"
    ]
  }
  ```
- The script constructs queries as `"{brand} menswear"` for each entry.
- Configurable via `--brands-file` CLI flag (default: `config/serper_brands.json`).

## Serper API Integration

- Endpoint: `POST https://google.serper.dev/shopping`
- Payload: `{"q": "Acne Studios menswear"}`
- Auth: `SERPER_API_KEY` env var, passed as `X-API-KEY` header.
- Rate limit: 0.5s between requests.
- Page 1 only (~10 results per brand). No pagination.
- Budget: ~20 brands × 2 credits = ~40 credits out of 2,400 free.

### Data Mapping

Each Serper shopping result maps to `CatalogItemCreate`:

| Serper field | DB field | Transformation |
|---|---|---|
| `link` | `item_id` | `serper_{sha256(link)[:12]}` |
| — | `domain` | `"fashion"` (hardcoded) |
| `title` | `title` | As-is |
| `price` | `price` | Parse currency string (e.g., `"$295.00"` → `295.00`). USD only; skip items with unparseable prices. |
| `imageUrl` | `image_url` | S3 URL after upload |
| `link` | `product_url` | As-is |
| — | `source` | `"serper"` |
| `title`, `price`, brand | `content_hash` | SHA-256 of `"{title}:{price}:{brand}:{category}".lower()` |
| brand (from config) | `attributes.brand` | Brand name from JSON config |
| — | `attributes.category` | `"menswear"` |

## Image Handling

- Download from Serper's `imageUrl` using `aiohttp`.
- Concurrency semaphore: 10 parallel downloads.
- Validation: valid image content type, max 5 MB.
- S3 upload path: `images/catalog/serper/{item_id}.jpg`
- Items with failed image downloads are skipped (warning logged).

## Bronze Layer Storage

Raw Serper JSON responses stored for audit:
- S3 path: `raw/catalog/serper/dt={YYYY-MM-DD}/brand={slug}/{HH-MM-SS}.json`

## DB Upsert & Deduplication

Same upsert pattern as Poshmark ingestion:

```sql
INSERT INTO catalog_items (item_id, domain, title, price, image_url, product_url, source, content_hash, attributes)
VALUES (...)
ON CONFLICT (item_id) DO UPDATE SET
    last_seen = NOW(),
    hit_count = catalog_items.hit_count + 1,
    price = EXCLUDED.price,
    image_url = COALESCE(EXCLUDED.image_url, catalog_items.image_url),
    content_hash = EXCLUDED.content_hash
RETURNING item_id, (xmax = 0) AS inserted
```

- Batch upsert per brand (all items from one search response).
- Dedup via `item_id` (primary) and `content_hash` (cross-source detection).

## Script CLI

File: `scripts/ingest_serper_catalog.py`

Flags:
- `--brands-file` — Path to brand list JSON (default: `config/serper_brands.json`)
- `--dry-run` — Parse and log without DB/S3 writes
- `--max-items` — Cap total items ingested across all brands

No checkpoint/resume — unnecessary for ~20 API calls.

## Environment

Requires:
- `DATABASE_URL` — PostgreSQL connection string
- `SERPER_API_KEY` — Serper.dev API key

Add `SERPER_API_KEY` to `.env.example`.

## Embeddings

Not generated during ingestion. Run `scripts/backfill_catalog_embeddings.py` separately after ingestion completes.

## Out of Scope

- Product page scraping (use Serper structured data only)
- Pagination beyond page 1
- Scheduled/recurring runs
- Gender filtering beyond query string ("menswear" in query)
