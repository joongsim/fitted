# Frontend Color Harmonization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate stray off-palette colors from `frontend/app.py` CSS and replace them with the canonical neobrutalist palette.

**Architecture:** All changes are surgical string replacements inside the `custom_css` Style block in `frontend/app.py`. No Python logic, no new files, no structural changes. Each task is a self-contained group of related CSS edits followed by a visual check and commit.

**Tech Stack:** FastHTML (Python), PicoCSS v2 (via CDN), inline CSS string in `frontend/app.py`

**Spec:** `docs/superpowers/specs/2026-03-24-frontend-color-harmonization-design.md`

---

## File Map

| File | Change |
|------|--------|
| `frontend/app.py` | All CSS edits — `custom_css` string only, lines ~37–562 |

No new files. No other files touched.

---

### Task 1: Fix Pico CSS variables and search button hover

The search button pulls its hover color from both an inline `background-color` override and two PicoCSS custom properties. All three sources of the wrong blue/green need updating together or the Pico framework will override the direct property.

**Files:**
- Modify: `frontend/app.py` (`:root`, `.search-form button`, `.search-form button:hover`)

- [ ] **Step 1: Edit `:root` — replace stray primary-hover green**

  In `frontend/app.py`, inside `:root { ... }`, change:
  ```
  --pico-primary-hover: #15803d;
  ```
  to:
  ```
  --pico-primary-hover: #000000;
  ```

- [ ] **Step 2: Edit `.search-form button` default state — fix Pico bg variable**

  Change:
  ```
  --pico-background-color: #16a34a;
  ```
  to:
  ```
  --pico-background-color: #95FB62;
  ```

- [ ] **Step 3: Edit `.search-form button:hover` — replace rogue blue**

  Change the entire `.search-form button:hover` block:
  ```css
      .search-form button:hover {
          background-color:rgb(0, 145, 255) !important;
          border: 2px solid #000000 !important;
          --pico-background-color: rgb(0, 145, 255);
      }
  ```
  to:
  ```css
      .search-form button:hover {
          background-color: #000000 !important;
          border: 2px solid #000000 !important;
          --pico-background-color: #000000;
          color: #95FB62;
      }
  ```

- [ ] **Step 4: Visual check**

  Start the frontend dev server:
  ```bash
  cd frontend && uvicorn app:app --host 127.0.0.1 --port 5001
  ```
  Open `http://localhost:5001`. Hover over the "Get Outfit" button. It should turn black with lime text. No blue should appear.

- [ ] **Step 5: Commit**

  ```bash
  git add frontend/app.py
  git commit -m "style: fix search button hover — replace rogue blue with invert pattern"
  ```

---

### Task 2: Fix shop button hover and filter bar

These share the same hover pattern (invert: black bg, lime text). The filter bar additionally needs its pill shape removed and borders upgraded.

**Files:**
- Modify: `frontend/app.py` (`.shop-btn:hover`, `.filter-btn`, `.filter-btn.active`, `.filter-btn:hover:not(.active)`)

- [ ] **Step 1: Edit `.shop-btn:hover`**

  Change:
  ```css
      .shop-btn:hover { background-color: #7de84a; }
  ```
  to:
  ```css
      .shop-btn:hover { background-color: #000000; color: #95FB62; }
  ```

- [ ] **Step 2: Edit `.filter-btn` — remove pill shape, fix border and colors**

  Change:
  ```css
      .filter-btn { padding: 0.35rem 0.85rem; border-radius: 9999px; border: 1px solid #cbd5e1; background: #f8fafc; color: #334155; cursor: pointer; font-size: 0.85rem; }
  ```
  to:
  ```css
      .filter-btn { padding: 0.35rem 0.85rem; border-radius: 0; border: 2px solid #000; background: #fff; color: #000; cursor: pointer; font-size: 0.85rem; font-weight: bold; }
  ```

- [ ] **Step 3: Edit `.filter-btn.active` — lime active state**

  Change:
  ```css
      .filter-btn.active { background: #1e293b; color: #f8fafc; border-color: #1e293b; }
  ```
  to:
  ```css
      .filter-btn.active { background: #95FB62; color: #000; border-color: #000; }
  ```

- [ ] **Step 4: Edit `.filter-btn:hover:not(.active)` — invert on hover**

  Change:
  ```css
      .filter-btn:hover:not(.active) { background: #e2e8f0; }
  ```
  to:
  ```css
      .filter-btn:hover:not(.active) { background: #000; color: #95FB62; }
  ```

- [ ] **Step 5: Visual check**

  Open `http://localhost:5001`. Navigate to `/recommendations`. Verify:
  - Filter chips have sharp corners (no pill shape)
  - Active chip is lime with black text
  - Hovering inactive chips inverts to black/lime
  - Shop button (if visible) inverts on hover

- [ ] **Step 6: Commit**

  ```bash
  git add frontend/app.py
  git commit -m "style: fix filter bar and shop button hover — remove pill shape, apply invert pattern"
  ```

---

### Task 3: Fix status banners

Error and success banners get the full neobrutalist treatment: 2px solid borders, no soft backgrounds, 0 border-radius (already 0), bold flat colors.

**Files:**
- Modify: `frontend/app.py` (`.error-message`, `.prefs-success`)

- [ ] **Step 1: Edit `.error-message`**

  Change:
  ```css
      .error-message {
          background-color: #fef2f2;
          border: 1px solid #fecaca;
          color: #dc2626;
  ```
  to:
  ```css
      .error-message {
          background-color: #ffffff;
          border: 2px solid #dc2626;
          color: #dc2626;
  ```

- [ ] **Step 2: Edit `.prefs-success`**

  Change:
  ```css
      .prefs-success {
          background-color: #f0fdf4;
          border: 1px solid #86efac;
          color: #15803d;
          padding: 0.75rem 1rem;
          margin-top: 1rem;
      }
  ```
  to:
  ```css
      .prefs-success {
          background-color: #95FB62;
          border: 2px solid #000;
          color: #000000;
          padding: 0.75rem 1rem;
          margin-top: 1rem;
      }
  ```

- [ ] **Step 3: Visual check**

  Navigate to `/preferences`. Submit invalid input to trigger the error banner — it should be white with a bold red border. Save preferences successfully — the success banner should be bright lime with black text and a black border.

  To trigger the error banner you can also look at the `.error-message` class directly at `/` by submitting an empty location.

- [ ] **Step 4: Commit**

  ```bash
  git add frontend/app.py
  git commit -m "style: fix status banners — neobrutalist borders, lime success, white error"
  ```

---

### Task 4: Fix image placeholders

All four placeholder elements (wardrobe card image, wardrobe card placeholder div, product card image, product card placeholder div) use cold gray `#f1f5f9`. Replace with warm cream `#fffbeb`. Also remove the off-palette `#e2e8f0` borders on wardrobe elements.

**Files:**
- Modify: `frontend/app.py` (`.wardrobe-card img`, `.wardrobe-card-placeholder`, `.product-card img`, `.product-card-placeholder`)

- [ ] **Step 1: Edit `.wardrobe-card img`**

  Change:
  ```css
      .wardrobe-card img {
          width: 100%;
          aspect-ratio: 1;
          object-fit: cover;
          display: block;
          background-color: #f1f5f9;
          border: 1px solid #e2e8f0;
          margin-bottom: 0.5rem;
      }
  ```
  to:
  ```css
      .wardrobe-card img {
          width: 100%;
          aspect-ratio: 1;
          object-fit: cover;
          display: block;
          background-color: #fffbeb;
          border: none;
          margin-bottom: 0.5rem;
      }
  ```

- [ ] **Step 2: Edit `.wardrobe-card-placeholder`**

  Change:
  ```css
      .wardrobe-card-placeholder {
          width: 100%;
          aspect-ratio: 1;
          background-color: #f1f5f9;
          display: flex;
          align-items: center;
          justify-content: center;
          font-size: 2rem;
          margin-bottom: 0.5rem;
          border: 1px solid #e2e8f0;
      }
  ```
  to:
  ```css
      .wardrobe-card-placeholder {
          width: 100%;
          aspect-ratio: 1;
          background-color: #fffbeb;
          display: flex;
          align-items: center;
          justify-content: center;
          font-size: 2rem;
          margin-bottom: 0.5rem;
          border: none;
      }
  ```

- [ ] **Step 3: Edit `.product-card img` background**

  Change:
  ```css
      .product-card img {
          width: 100%;
          aspect-ratio: 1;
          object-fit: cover;
          display: block;
          background-color: #f1f5f9;
          margin-bottom: 0.5rem;
      }
  ```
  to:
  ```css
      .product-card img {
          width: 100%;
          aspect-ratio: 1;
          object-fit: cover;
          display: block;
          background-color: #fffbeb;
          margin-bottom: 0.5rem;
      }
  ```

- [ ] **Step 4: Edit `.product-card-placeholder` background**

  Change:
  ```css
      .product-card-placeholder {
          width: 100%;
          aspect-ratio: 1;
          background-color: #f1f5f9;
          display: flex;
          align-items: center;
          justify-content: center;
          font-size: 1.5rem;
          margin-bottom: 0.5rem;
      }
  ```
  to:
  ```css
      .product-card-placeholder {
          width: 100%;
          aspect-ratio: 1;
          background-color: #fffbeb;
          display: flex;
          align-items: center;
          justify-content: center;
          font-size: 1.5rem;
          margin-bottom: 0.5rem;
      }
  ```

- [ ] **Step 5: Visual check**

  Navigate to `/wardrobe`. Any wardrobe items without images should show a warm cream placeholder instead of cold gray. Navigate to `/recommendations` — product card image areas should also be warm cream.

- [ ] **Step 6: Commit**

  ```bash
  git add frontend/app.py
  git commit -m "style: warm up image placeholders — replace cold gray with cream background"
  ```

---

### Task 5: Fix accent text, loading spinner, and action button hover

All remaining `#16a34a` forest green instances are replaced. The nav active link gets lime (visible accent on dark-on-cream nav), auth links and data text get black. The spinner gets lime. The product action button hover gets the invert pattern.

**Files:**
- Modify: `frontend/app.py` (`.nav-bar a.active`, `.retro-card`, `.product-card-price`, `.auth-link a`, `.loading`, `.product-card-action-btn:hover`)

- [ ] **Step 1: Edit `.nav-bar a.active`**

  Change:
  ```css
      .nav-bar a.active { color: #16a34a; }
  ```
  to:
  ```css
      .nav-bar a.active { color: #95FB62; }
  ```

- [ ] **Step 2: Edit `.retro-card` — remove green text color**

  Change:
  ```css
      .retro-card {
          background-color: #ffffff;
          border: 2px solid #000000;
          border-radius: 0;
          color: #16a34a;
          box-shadow: none;
      }
  ```
  to:
  ```css
      .retro-card {
          background-color: #ffffff;
          border: 2px solid #000000;
          border-radius: 0;
          color: #000000;
          box-shadow: none;
      }
  ```

- [ ] **Step 3: Edit `.product-card-price`**

  Change:
  ```css
      .product-card-price {
          font-size: 0.875rem;
          color: #16a34a;
          font-weight: bold;
          margin-bottom: 0.5rem;
      }
  ```
  to:
  ```css
      .product-card-price {
          font-size: 0.875rem;
          color: #000000;
          font-weight: bold;
          margin-bottom: 0.5rem;
      }
  ```

- [ ] **Step 4: Edit `.auth-link a`**

  Change:
  ```css
      .auth-link a { color: #16a34a; font-weight: bold; }
  ```
  to:
  ```css
      .auth-link a { color: #000000; font-weight: bold; }
  ```

- [ ] **Step 5: Edit `.loading` spinner**

  Change:
  ```css
      .loading {
          display: inline-block;
          width: 1rem;
          height: 1rem;
          border: 2px solid #16a34a;
          border-radius: 50%;
          border-top-color: transparent;
          animation: spin 0.8s linear infinite;
          cursor: pointer;
          user-select: none;
      }
  ```
  to:
  ```css
      .loading {
          display: inline-block;
          width: 1rem;
          height: 1rem;
          border: 2px solid #95FB62;
          border-radius: 50%;
          border-top-color: transparent;
          animation: spin 0.8s linear infinite;
          cursor: pointer;
          user-select: none;
      }
  ```

- [ ] **Step 6: Edit `.product-card-action-btn:hover`**

  Change:
  ```css
      .product-card-action-btn:hover { background-color: #f1f5f9; }
  ```
  to:
  ```css
      .product-card-action-btn:hover { background-color: #000; color: #95FB62; }
  ```

- [ ] **Step 7: Visual check**

  - `/` — submit a weather request; observe the loading spinner is lime while waiting
  - `/login` or `/register` — "Log in" / "Register here" links should be black, not green
  - `/recommendations` — active nav link should be lime; weather card and metric card text should be black, not green; product prices should be black
  - All pages — active nav link (e.g. "Recs") is lime

- [ ] **Step 8: Commit**

  ```bash
  git add frontend/app.py
  git commit -m "style: replace remaining #16a34a forest green — nav lime, text black, spinner lime"
  ```
