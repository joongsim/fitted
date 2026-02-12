import os
from contextlib import asynccontextmanager
from psycopg_pool import AsyncConnectionPool

# DATABASE_URL should be set in .env or environment
DATABASE_URL = os.environ.get("DATABASE_URL")

pool: AsyncConnectionPool | None = None

async def init_pool():
    """Initialize connection pool. Call on app startup."""
    global pool
    if not DATABASE_URL:
        print("Warning: DATABASE_URL not set. Database pool not initialized.")
        return
    
    pool = AsyncConnectionPool(
        conninfo=DATABASE_URL, 
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
