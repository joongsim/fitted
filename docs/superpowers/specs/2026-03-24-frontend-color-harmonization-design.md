# Frontend Color Harmonization Design

**Date:** 2026-03-24
**Status:** Approved
**Scope:** Surgical CSS patch — 14 targeted value replacements in `frontend/app.py`

## Goal

Harmonize the existing bright neobrutalist palette by eliminating stray colors that crept in over time. The design language stays exactly the same; only off-palette values are corrected.

## Canonical Palette

| Token | Value | Role |
|-------|-------|------|
| Warm cream | `#fffbeb` | Page background |
| Lime | `#95FB62` | Primary CTA, active states, success bg, spinner |
| White | `#ffffff` | Card background |
| Black | `#000000` | Borders, text, hover bg |
| Muted | `#64748b` | Secondary labels, hints |
| Error red | `#dc2626` | Error border and text only |

**Hover / active pattern:** invert — black background + lime text. No secondary accent colors.

## Changes

### 1. Search button hover (`.search-form button:hover`)
- `background-color: rgb(0, 145, 255)` → `#000000`
- `--pico-background-color: rgb(0, 145, 255)` → `#000000`
- Add `color: #95FB62`

### 2. Shop button hover (`.shop-btn:hover`)
- `background-color: #7de84a` → `#000000`
- Add `color: #95FB62`

### 3. Filter bar (`.filter-btn`, `.filter-btn.active`, `.filter-btn:hover:not(.active)`)
- Remove `border-radius: 9999px`
- `border: 1px solid #cbd5e1` → `border: 2px solid #000`
- `background: #f8fafc` → `background: #fff`
- `color: #334155` → `color: #000`
- Active: `background: #1e293b` → `#95FB62`; `color: #f8fafc` → `#000`; `border-color: #1e293b` → `#000`
- Hover: `background: #e2e8f0` → `#000`; add `color: #95FB62`

### 4. Error banner (`.error-message`)
- `border: 1px solid #fecaca` → `border: 2px solid #dc2626`
- `background-color: #fef2f2` → `#ffffff`

### 5. Success banner (`.prefs-success`)
- `border: 1px solid #86efac` → `border: 2px solid #000`
- `background-color: #f0fdf4` → `#95FB62`
- `color: #15803d` → `#000000`

### 6. Image placeholders
Targets: `.wardrobe-card-placeholder`, `.product-card-placeholder`, `.wardrobe-card img` background, `.product-card img` background
- `background-color: #f1f5f9` → `#fffbeb`
- `.wardrobe-card img` border: `1px solid #e2e8f0` → remove (or `none`)

### 7. Accent text — auth link and retro-card
- `.auth-link a { color: #16a34a }` → `color: #000000`
- `.retro-card { color: #16a34a }` → `color: #000000`
- `.product-card-price { color: #16a34a }` → `color: #000000`

### 8. Loading spinner (`.loading`)
- `border: 2px solid #16a34a` → `border: 2px solid #95FB62`

## What Is Not Changing

- Page/body background (`#fffbeb`)
- Black borders (`2px solid #000`) on cards
- `#64748b` muted text on labels/hints
- Primary button default state (lime bg, black text, black border)
- The `#dc2626` red — retained as semantic error color only
- All layout, spacing, typography

## File Changed

`frontend/app.py` — the `custom_css` string only. No Python logic changes.

## Testing

Visual check: load `/`, `/wardrobe`, `/recommendations`, `/preferences`, `/login` and verify:
- Buttons invert correctly on hover
- Filter chips are sharp-cornered with lime active state
- Error and success banners are bold/flat
- Wardrobe and product image placeholders have warm cream background
- Loading spinner is lime
