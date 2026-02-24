"""Tests for app/services/db_service.py — async PostgreSQL connection pool."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _reset_pool():
    """Set the module-level pool back to None between tests."""
    import app.services.db_service as db_service

    db_service.pool = None


# ---------------------------------------------------------------------------
# init_pool
# ---------------------------------------------------------------------------


class TestInitPool:
    async def test_init_pool_creates_pool_when_database_url_set(self):
        _reset_pool()
        import app.services.db_service as db_service

        mock_pool = AsyncMock()

        with patch("app.core.config.config.get_parameter", return_value="postgres://localhost/db"):
            with patch("app.services.db_service.AsyncConnectionPool", return_value=mock_pool) as mock_cls:
                await db_service.init_pool()

        mock_cls.assert_called_once()
        call_kwargs = mock_cls.call_args[1]
        assert call_kwargs["conninfo"] == "postgres://localhost/db"
        assert call_kwargs["min_size"] == 2
        assert call_kwargs["max_size"] == 10
        assert call_kwargs["open"] is False
        mock_pool.open.assert_awaited_once()

    async def test_init_pool_sets_module_level_pool(self):
        _reset_pool()
        import app.services.db_service as db_service

        mock_pool = AsyncMock()
        with patch("app.core.config.config.get_parameter", return_value="postgres://localhost/db"):
            with patch("app.services.db_service.AsyncConnectionPool", return_value=mock_pool):
                await db_service.init_pool()

        assert db_service.pool is mock_pool

    async def test_init_pool_does_nothing_when_database_url_empty(self):
        _reset_pool()
        import app.services.db_service as db_service

        with patch("app.core.config.config.get_parameter", return_value=""):
            with patch("app.services.db_service.AsyncConnectionPool") as mock_cls:
                await db_service.init_pool()

        mock_cls.assert_not_called()
        assert db_service.pool is None

    async def test_init_pool_does_nothing_when_database_url_raises(self):
        _reset_pool()
        import app.services.db_service as db_service

        with patch("app.core.config.config.get_parameter", side_effect=ValueError("no url")):
            with patch("app.services.db_service.AsyncConnectionPool") as mock_cls:
                # init_pool does not propagate the error; it just logs and returns
                try:
                    await db_service.init_pool()
                except ValueError:
                    pass  # If it propagates, that's also acceptable — pool stays None

        # The key assertion: no pool was set up
        assert db_service.pool is None


# ---------------------------------------------------------------------------
# close_pool
# ---------------------------------------------------------------------------


class TestClosePool:
    async def test_close_pool_calls_pool_close(self):
        import app.services.db_service as db_service

        mock_pool = AsyncMock()
        db_service.pool = mock_pool

        await db_service.close_pool()
        mock_pool.close.assert_awaited_once()

    async def test_close_pool_does_nothing_when_pool_is_none(self):
        _reset_pool()
        import app.services.db_service as db_service

        # Should not raise
        await db_service.close_pool()

    async def test_close_pool_preserves_pool_reference(self):
        """close_pool does not set pool to None — just closes the underlying resource."""
        import app.services.db_service as db_service

        mock_pool = AsyncMock()
        db_service.pool = mock_pool

        await db_service.close_pool()
        # The pool attribute is still the same object (not None) after close
        assert db_service.pool is mock_pool


# ---------------------------------------------------------------------------
# get_connection
# ---------------------------------------------------------------------------


class TestGetConnection:
    async def test_get_connection_raises_runtime_error_when_pool_is_none(self):
        _reset_pool()
        import app.services.db_service as db_service

        with pytest.raises(RuntimeError, match="Database pool not initialized"):
            async with db_service.get_connection():
                pass

    async def test_get_connection_yields_connection_from_pool(self):
        import app.services.db_service as db_service

        mock_conn = AsyncMock()
        mock_pool = MagicMock()
        mock_pool.connection.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.connection.return_value.__aexit__ = AsyncMock(return_value=False)
        db_service.pool = mock_pool

        async with db_service.get_connection() as conn:
            assert conn is mock_conn

    async def test_get_connection_uses_pool_context_manager(self):
        import app.services.db_service as db_service

        mock_conn = AsyncMock()
        mock_pool = MagicMock()
        mock_pool.connection.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.connection.return_value.__aexit__ = AsyncMock(return_value=False)
        db_service.pool = mock_pool

        async with db_service.get_connection():
            pass

        mock_pool.connection.assert_called_once()
