import os
from contextlib import asynccontextmanager
from psycopg_pool import AsyncConnectionPool
from app.core.config import config

pool: AsyncConnectionPool | None = None

async def init_pool():
    """Initialize connection pool. Call on app startup."""
    global pool
    database_url = config.database_url
    if not database_url:
        print("Warning: DATABASE_URL not set. Database pool not initialized.")
        return

    pool = AsyncConnectionPool(
        conninfo=database_url,
        min_size=2,
        max_size=10,
        open=False
    )
    await pool.open()
    print("Database connection pool initialized.")

async def close_pool():
    """Close pool. Call on app shutdown."""
    global pool
    if pool:
        await pool.close()
        print("Database connection pool closed.")

@asynccontextmanager
async def get_connection():
    """Get a connection from the pool."""
    if pool is None:
        raise RuntimeError("Database pool not initialized. Call init_pool() first.")
    
    async with pool.connection() as conn:
        yield conn
