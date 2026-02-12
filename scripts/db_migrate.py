import os
import psycopg
from psycopg import sql

# Database connection details from environment or SSM
# In Phase 2, we assume DATABASE_URL is provided or we construct it.
DATABASE_URL = os.environ.get("DATABASE_URL")

SCHEMA_SQL = """
-- Enable pgvector extension (available on RDS PostgreSQL 15+)
CREATE EXTENSION IF NOT EXISTS vector;

-- Wardrobe items table
CREATE TABLE IF NOT EXISTS wardrobe_items (
    item_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
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
-- Created now so it's ready when embeddings are added later
CREATE INDEX IF NOT EXISTS idx_wardrobe_embedding
    ON wardrobe_items USING hnsw (embedding vector_cosine_ops);

-- Category index for filtered queries
CREATE INDEX IF NOT EXISTS idx_wardrobe_category
    ON wardrobe_items(category);

-- Auto-update updated_at on row changes
CREATE OR REPLACE FUNCTION update_modified_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS set_updated_at ON wardrobe_items;
CREATE TRIGGER set_updated_at
    BEFORE UPDATE ON wardrobe_items
    FOR EACH ROW EXECUTE FUNCTION update_modified_column();
"""

def migrate():
    if not DATABASE_URL:
        print("Error: DATABASE_URL environment variable not set.")
        return

    print(f"Connecting to database...")
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
