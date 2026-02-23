import logging
import os
from contextlib import asynccontextmanager

from psycopg_pool import AsyncConnectionPool

from app.core.config import config

logger = logging.getLogger(__name__)

pool: AsyncConnectionPool | None = None


async def init_pool() -> None:
    """Initialize connection pool. Call on app startup."""
    global pool
    database_url = config.database_url
    if not database_url:
        logger.critical(
            "DATABASE_URL not set — database pool not initialized. "
            "The application will fail on any DB operation."
        )
        return

    pool = AsyncConnectionPool(
        conninfo=database_url,
        min_size=2,
        max_size=10,
        open=False,
    )
    await pool.open()
    logger.info("Database connection pool initialized (min=2, max=10).")


async def close_pool() -> None:
    """Close pool. Call on app shutdown."""
    global pool
    if pool:
        await pool.close()
        logger.info("Database connection pool closed.")


@asynccontextmanager
async def get_connection():
    """Get a connection from the pool."""
    if pool is None:
        logger.error(
            "get_connection() called before pool was initialized. "
            "Ensure init_pool() runs at startup.",
            exc_info=True,
        )
        raise RuntimeError("Database pool not initialized. Call init_pool() first.")

    async with pool.connection() as conn:
        yield conn
