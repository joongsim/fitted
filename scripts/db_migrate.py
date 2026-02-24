import os
import sys
import psycopg
from psycopg import sql

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app.core.config import config

# Database connection URL from SSM (or env for local dev)
DATABASE_URL = config.database_url

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
    embedding VECTOR(384),         -- NULL until embeddings phase
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

-- Product catalog — Poshmark dev seed + future SerpAPI/production sources
-- NOTE: VECTOR(512) for CLIP ViT-B/32 (512-dim).
-- wardrobe_items currently uses VECTOR(384) — to be aligned in a future migration
-- when the CLIP embedding service is deployed and wardrobe embeddings are backfilled
-- (all embeddings are NULL today, so the ALTER COLUMN will be safe at that point).
CREATE TABLE IF NOT EXISTS catalog_items (
    item_id       VARCHAR PRIMARY KEY,           -- Poshmark listing ID
    domain        VARCHAR NOT NULL DEFAULT 'fashion',
    title         TEXT,
    price         FLOAT,
    image_url     TEXT,                          -- S3 URL after image copy
    product_url   TEXT,
    source        VARCHAR,                       -- 'poshmark_seed' | 'serpapi' | ...
    embedding     VECTOR(512),                   -- NULL until CLIP service is built
    content_hash  VARCHAR,                       -- SHA-256 of title:price:brand:category
    attributes    JSONB DEFAULT '{}',            -- brand, size, condition, colors, etc.
    first_seen    TIMESTAMPTZ DEFAULT NOW(),
    last_seen     TIMESTAMPTZ DEFAULT NOW(),
    hit_count     INT DEFAULT 1,
    model_version VARCHAR
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
"""


def migrate():
    if not DATABASE_URL:
        print("Error: DATABASE_URL environment variable not set.")
        return

    print("Connecting to database...")
    try:
        with psycopg.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                print("Applying schema...")
                cur.execute(SCHEMA_SQL)
                conn.commit()
                print("Migration successful.")
    except Exception as e:
        print(f"Migration failed: {e}")


if __name__ == "__main__":
    migrate()
