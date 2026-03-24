# Frontend Color Harmonization Design

**Date:** 2026-03-24
**Status:** Approved
**Scope:** Surgical CSS patch — targeted value replacements in `frontend/app.py`

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
| Error red | `#dc2626` | Error border and text only (also: delete button) |

**Hover / active pattern:** invert — black background + lime text. No secondary accent colors.

## Changes

### 1. Pico CSS variable overrides (`:root` and `.search-form button`)
- `:root { --pico-primary-hover: #15803d }` → `--pico-primary-hover: #000000`
- `.search-form button { --pico-background-color: #16a34a }` → `--pico-background-color: #95FB62`

### 2. Search button hover (`.search-form button:hover`)
- `background-color: rgb(0, 145, 255)` → `#000000`
- `--pico-background-color: rgb(0, 145, 255)` → `#000000`
- Add `color: #95FB62`

### 3. Shop button hover (`.shop-btn:hover`)
- `background-color: #7de84a` → `#000000`
- Add `color: #95FB62`

### 4. Filter bar (`.filter-btn`, `.filter-btn.active`, `.filter-btn:hover:not(.active)`)
- Remove `border-radius: 9999px`
- `border: 1px solid #cbd5e1` → `border: 2px solid #000`
- `background: #f8fafc` → `background: #fff`
- `color: #334155` → `color: #000`
- Active: `background: #1e293b` → `#95FB62`; `color: #f8fafc` → `#000`; `border-color: #1e293b` → `#000`
- Hover: `background: #e2e8f0` → `#000`; add `color: #95FB62`

### 5. Error banner (`.error-message`)
- `border: 1px solid #fecaca` → `border: 2px solid #dc2626`
- `background-color: #fef2f2` → `#ffffff`

### 6. Success banner (`.prefs-success`)
- `border: 1px solid #86efac` → `border: 2px solid #000`
- `background-color: #f0fdf4` → `#95FB62`
- `color: #15803d` → `#000000`

### 7. Image placeholders
Targets: `.wardrobe-card-placeholder`, `.product-card-placeholder`, `.wardrobe-card img` background, `.product-card img` background
- `background-color: #f1f5f9` → `#fffbeb` (all four)
- `.wardrobe-card-placeholder` border: `1px solid #e2e8f0` → `none`
- `.wardrobe-card img` border: `1px solid #e2e8f0` → `none`
- Note: `.product-card img` and `.product-card-placeholder` have no border in current CSS — background-color change only

### 8. Accent text colors
- `.nav-bar a.active { color: #16a34a }` → `color: #95FB62`
- `.auth-link a { color: #16a34a }` → `color: #000000`
- `.retro-card { color: #16a34a }` → `color: #000000`
- `.product-card-price { color: #16a34a }` → `color: #000000`

### 9. Loading spinner (`.loading`)
- `border: 2px solid #16a34a` → `border: 2px solid #95FB62`

### 10. Action button hover
- `.product-card-action-btn:hover { background-color: #f1f5f9 }` → `background-color: #000; color: #95FB62`

## What Is Not Changing

- Page/body background (`#fffbeb`)
- Black borders (`2px solid #000`) on cards
- `#64748b` muted text on labels/hints
- Primary button default state (lime bg, black text, black border)
- `#dc2626` red — retained as semantic error color for `.error-message` border/text and `.wardrobe-card-delete` (intentional: delete is a destructive action, red is appropriate)
- `.wardrobe-card-delete:hover { background-color: #fef2f2 }` — intentionally retained as a subtle red tint on hover for a destructive action
- All layout, spacing, typography

## File Changed

`frontend/app.py` — the `custom_css` string only. No Python logic changes.

## Testing

Visual check: load each page and verify:

- `/` — search button inverts (black bg, lime text) on hover; loading spinner is lime
- `/login`, `/register` — auth links are black, not green
- `/wardrobe` — image placeholders are warm cream; delete button still red; wardrobe upload button inverts on hover
- `/recommendations` — filter chips are sharp-cornered; active chip is lime with black text; chips invert on hover; nav active link is lime; weather card and metric card text is black, not green
- `/preferences` — prefs save button inverts on hover; success banner is lime/black; error banner is white/red-bordered
- All pages — active nav link is lime, not forest green
