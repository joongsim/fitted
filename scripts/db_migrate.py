import os
import sys
import psycopg

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app.core.config import config

SCHEMA_SQL = """
-- Enable extensions
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Users table
CREATE TABLE IF NOT EXISTS users (
    user_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    email VARCHAR(255) UNIQUE NOT NULL,
    hashed_password VARCHAR(255) NOT NULL,
    full_name VARCHAR(255),
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    last_login TIMESTAMPTZ
);

-- User preferences table (JSONB for flexibility)
CREATE TABLE IF NOT EXISTS user_preferences (
    user_id UUID PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
    style_preferences JSONB DEFAULT '{
        "colors": [],
        "styles": [],
        "occasions": [],
        "avoid": []
    }',
    size_info JSONB DEFAULT '{
        "top": null,
        "bottom": null,
        "shoe": null
    }',
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Wardrobe items table (Updated with user_id)
CREATE TABLE IF NOT EXISTS wardrobe_items (
    item_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID REFERENCES users(user_id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    description TEXT DEFAULT '',
    category VARCHAR(50),          -- NULL until vision service auto-populates
    image_s3_key VARCHAR(500),     -- e.g. "wardrobe-images/{uuid}.jpg"
    tags TEXT[] DEFAULT '{}',
    classification JSONB,          -- NULL until vision service
    embedding VECTOR(512),         -- NULL until embeddings phase; CLIP ViT-B/32 (512-dim)
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- HNSW index for vector search (works on empty tables, unlike ivfflat)
CREATE INDEX IF NOT EXISTS idx_wardrobe_embedding
    ON wardrobe_items USING hnsw (embedding vector_cosine_ops);

-- Category index for filtered queries
CREATE INDEX IF NOT EXISTS idx_wardrobe_category
    ON wardrobe_items(category);

-- User ID index for fast lookups
CREATE INDEX IF NOT EXISTS idx_wardrobe_user_id
    ON wardrobe_items(user_id);

-- Email index for fast login
CREATE INDEX IF NOT EXISTS idx_users_email
    ON users(email);

-- Auto-update updated_at on row changes
CREATE OR REPLACE FUNCTION update_modified_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Set up triggers for all tables
DROP TRIGGER IF EXISTS set_updated_at ON wardrobe_items;
CREATE TRIGGER set_updated_at
    BEFORE UPDATE ON wardrobe_items
    FOR EACH ROW EXECUTE FUNCTION update_modified_column();

DROP TRIGGER IF EXISTS set_updated_at_prefs ON user_preferences;
CREATE TRIGGER set_updated_at_prefs
    BEFORE UPDATE ON user_preferences
    FOR EACH ROW EXECUTE FUNCTION update_modified_column();

-- Align wardrobe_items.embedding to VECTOR(512) (CLIP ViT-B/32).
-- Only executes when the column dimension differs from 512 (safe on re-runs).
-- The HNSW index must be dropped first; pgvector won't ALTER TYPE with an index attached.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_attribute a
        JOIN pg_class c ON c.oid = a.attrelid
        WHERE c.relname = 'wardrobe_items'
          AND a.attname = 'embedding'
          AND a.atttypmod <> 512
    ) THEN
        DROP INDEX IF EXISTS idx_wardrobe_embedding;
        ALTER TABLE wardrobe_items ALTER COLUMN embedding TYPE VECTOR(512);
    END IF;
END $$;

-- Product catalog — Poshmark dev seed + future SerpAPI/production sources
CREATE TABLE IF NOT EXISTS catalog_items (
    item_id       TEXT PRIMARY KEY,              -- Poshmark listing ID
    domain        VARCHAR NOT NULL DEFAULT 'fashion',
    title         TEXT,
    price         NUMERIC(10, 2),               -- monetary value; avoid float imprecision
    image_url     TEXT,                          -- S3 URL after image copy
    product_url   TEXT,
    source        TEXT,                          -- 'poshmark_seed' | 'serpapi' | ...
    embedding     VECTOR(512),                   -- NULL until CLIP service is built
    content_hash  CHAR(64),                      -- SHA-256 of title:price:brand:category
    attributes    JSONB DEFAULT '{}',            -- brand, size, condition, colors, etc.
    first_seen    TIMESTAMPTZ DEFAULT NOW(),
    last_seen     TIMESTAMPTZ DEFAULT NOW(),
    hit_count     INT DEFAULT 1,
    model_version TEXT
);

-- HNSW index for vector similarity search (works on empty tables + NULL embeddings)
CREATE INDEX IF NOT EXISTS idx_catalog_embedding
    ON catalog_items USING hnsw (embedding vector_cosine_ops);

-- Indexes for common filter patterns used in candidate retrieval
CREATE INDEX IF NOT EXISTS idx_catalog_source
    ON catalog_items(source);

CREATE INDEX IF NOT EXISTS idx_catalog_domain
    ON catalog_items(domain);

-- GIN index for JSONB attribute queries (e.g., filter by brand, category, size)
CREATE INDEX IF NOT EXISTS idx_catalog_attributes
    ON catalog_items USING gin (attributes);

-- Content hash index for deduplication queries
CREATE INDEX IF NOT EXISTS idx_catalog_content_hash
    ON catalog_items(content_hash);

-- Query cache table for storing recent queries, their embeddings, and results
CREATE TABLE IF NOT EXISTS query_cache (
    cache_id        UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    query_hash      CHAR(64) UNIQUE NOT NULL,
    query_text      TEXT,
    query_embedding VECTOR(512),
    s3_key          TEXT,
    expires_at      TIMESTAMPTZ
);

-- HNSW index for query embedding similarity search (works on empty tables + NULL embeddings)
CREATE INDEX IF NOT EXISTS idx_query_cache_embedding
    ON query_cache USING hnsw (query_embedding vector_cosine_ops);

-- Index on expires_at for efficient cleanup of expired cache entries
CREATE INDEX IF NOT EXISTS idx_query_cache_expires
    ON query_cache (expires_at);

-- User interaction signals — raw click/save/dismiss events from recommendation results.
-- These feed the Week 8 training pipeline (triplet construction for TripletMarginLoss).
CREATE TABLE IF NOT EXISTS user_interactions (
    interaction_id   UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id          UUID REFERENCES users(user_id) ON DELETE CASCADE,
    item_id          TEXT REFERENCES catalog_items(item_id),
    interaction_type VARCHAR NOT NULL CHECK (interaction_type IN ('click', 'save', 'dismiss')),
    weather_context  JSONB DEFAULT '{}',  -- {temp_c, condition, location} at time of interaction
    query_text       TEXT,                -- the LLM-generated search query that surfaced this item
    created_at       TIMESTAMPTZ DEFAULT NOW()
);

-- Composite index: user timeline queries used by the training pipeline
CREATE INDEX IF NOT EXISTS idx_interactions_user
    ON user_interactions (user_id, created_at DESC);

-- Pairwise preference signals — "I prefer item A over item B".
-- These feed the Bradley-Terry preference reranker (preference_reranker.py).
CREATE TABLE IF NOT EXISTS preference_pairs (
    pair_id    UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id    UUID REFERENCES users(user_id) ON DELETE CASCADE,
    item_a_id  TEXT REFERENCES catalog_items(item_id),
    item_b_id  TEXT REFERENCES catalog_items(item_id),
    preferred  VARCHAR NOT NULL CHECK (preferred IN ('a', 'b')),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Index for per-user Bradley-Terry model fitting
CREATE INDEX IF NOT EXISTS idx_pairs_user
    ON preference_pairs (user_id);

-- Affiliate click tracking — server-side redirect log for conversion attribution.
-- Every product card click goes through GET /r/{click_id} which marks clicked_at.
-- Network-specific affiliate URL is stored here so we don't expose query params in HTML.
CREATE TABLE IF NOT EXISTS affiliate_clicks (
    click_id      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id       UUID REFERENCES users(user_id) ON DELETE CASCADE,
    item_id       TEXT REFERENCES catalog_items(item_id),
    original_url  TEXT NOT NULL,
    affiliate_url TEXT NOT NULL,
    network       VARCHAR NOT NULL DEFAULT 'none',  -- 'amazon' | 'shopstyle' | 'rakuten' | 'none'
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    clicked_at    TIMESTAMPTZ                        -- NULL until the redirect fires
);

-- Index for analytics: affiliate revenue per user per day
CREATE INDEX IF NOT EXISTS idx_affiliate_clicks_user
    ON affiliate_clicks (user_id, created_at DESC);

-- Index for fast redirect lookups by click_id (primary key covers this; added for clarity)
CREATE INDEX IF NOT EXISTS idx_affiliate_clicks_clicked_at
    ON affiliate_clicks (clicked_at) WHERE clicked_at IS NOT NULL;
"""


def _split_statements(sql: str) -> list[str]:
    """Split a SQL script into individual statements, respecting dollar-quoted blocks.

    A naive split on ';' breaks PL/pgSQL functions whose bodies contain semicolons
    inside $$ ... $$ dollar-quote delimiters.  This function tracks that quoting state
    so each returned string is exactly one complete, executable SQL statement.
    """
    statements: list[str] = []
    buf: list[str] = []
    in_dollar_quote = False

    for line in sql.splitlines():
        if "$$" in line:
            # An odd number of $$ occurrences on a single line toggles the quoting state.
            if line.count("$$") % 2 == 1:
                in_dollar_quote = not in_dollar_quote
        buf.append(line)
        if not in_dollar_quote and line.rstrip().endswith(";"):
            stmt = "\n".join(buf).strip()
            if stmt:
                statements.append(stmt)
            buf = []

    remainder = "\n".join(buf).strip()
    if remainder:
        statements.append(remainder)

    return statements


def migrate() -> None:
    """Apply the database schema migrations."""
    try:
        database_url = config.database_url
    except Exception as e:
        print(f"Error: could not load DATABASE_URL — {e}", file=sys.stderr)
        sys.exit(1)

    print("Connecting to database...")
    try:
        with psycopg.connect(database_url, autocommit=True) as conn:
            with conn.cursor() as cur:
                conn.autocommit = True
                print("Applying schema...")
                for statement in _split_statements(SCHEMA_SQL):
                    cur.execute(statement)
                print("Migration successful.")
    except Exception as e:
        print(f"Migration failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    migrate()
