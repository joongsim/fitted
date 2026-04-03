# Catalog Data Sources — Design Spec

**Date:** 2026-03-26
**Issue:** [#71](https://github.com/joongsim/fitted/issues/71)
**Status:** Approved

## Problem

The current Poshmark/RapidAPI catalog source has two blocking issues:

- **Coverage**: secondhand-only listings, limited query set (~20 queries), inconsistent category labels (fixes needed for issue #70)
- **Cost**: RapidAPI quota consumed per search query; expensive to scale

## Approach

Two phases:

- **Phase 1 (now):** Ingest DeepFashion2 as a free, large-scale dev catalog
- **Phase 2 (later):** Replace/supplement with affiliate product feeds (CJ / Rakuten / ShareASale) for real purchasable items

---

## Phase 1 — DeepFashion2 Dev Catalog

### Dataset

[DeepFashion2](https://github.com/switchablenorms/DeepFashion2) — ~800k clothing images with fine-grained category annotations. Free for non-commercial use. No gender filtering applied (adds overhead with no meaningful benefit; the two-tower model surfaces relevant items via preference embeddings).

### Ingestion pipeline

New script: `scripts/ingest_deepfashion2_catalog.py`, modeled on `ingest_poshmark_dev_catalog.py`.

Steps:
1. Read DeepFashion2 annotation JSON files (one per image)
2. Normalize DeepFashion2 category label → app filter taxonomy (see below)
3. Upload image to S3 under `catalog/deepfashion2/<item_id>.jpg`
4. Upsert into `catalog_items`:
   - `source = 'deepfashion2'`
   - `domain = 'fashion'`
   - `price = 0.0`
   - `product_url = ''`
   - `attributes.category` = normalized category string
5. After ingestion, run existing `scripts/backfill_catalog_embeddings.py` to generate CLIP embeddings

Supports `--dry-run` and `--max-items` flags, consistent with the existing ingestion script pattern.

### Category normalization

DeepFashion2 has 13 clothing categories. Mapping to app filter taxonomy:

| DeepFashion2 label | App category |
|---|---|
| short sleeve top | `tops` |
| long sleeve top | `tops` |
| vest | `tops` |
| sling | `tops` |
| short sleeve dress | `tops` |
| long sleeve dress | `tops` |
| vest dress | `tops` |
| sling dress | `tops` |
| shorts | `bottoms` |
| trousers | `bottoms` |
| skirt | `bottoms` |
| short sleeve outwear | `outerwear` |
| long sleeve outwear | `outerwear` |

`shoes` and `accessories` filters will return no results until Phase 2. This is expected and acceptable for dev.

### Side effects

- Fixes issue #70 (category filter mismatch) — `attributes->>'category'` values will be lowercase and match frontend filter values exactly
- DeepFashion2 images are also suitable training data for `scripts/pretrain_item_tower.py` — higher-quality item tower embeddings as a secondary benefit

---

## Phase 2 — Affiliate Product Feeds

Apply to affiliate programs at major fashion retailers via CJ Affiliate, Rakuten, or ShareASale. Once approved, retailers provide bulk product data feeds (CSV/XML) containing: title, price, image URL, product URL (affiliate), category.

New script: `scripts/ingest_affiliate_catalog.py`

- Parses retailer feed format (CSV/XML varies by program)
- Normalizes category to app taxonomy
- Upserts into `catalog_items` with `source = 'affiliate_<network>'` (e.g. `affiliate_cj`, `affiliate_rakuten`)
- Periodic re-ingestion (e.g. weekly cron) to keep catalog fresh

Phase 2 is out of scope for the current implementation plan. It is tracked in issue #71.

---

## Schema impact

No schema changes required. The `catalog_items` table already supports:
- `source TEXT` — distinguishes `poshmark_seed`, `deepfashion2`, `affiliate_cj`, etc.
- `attributes JSONB` — stores normalized `category`, and any additional fields
- `price NUMERIC` — `0.0` for DeepFashion2 items
- `product_url TEXT` — empty string for DeepFashion2 items

---

## Out of scope

- Gender filtering at ingest time (overhead not justified; model handles relevance)
- UI changes to handle items with no purchase URL (Phase 2 concern)
- Automated feed refresh (Phase 2)
