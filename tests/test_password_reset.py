"""Tests for password reset feature."""


def test_migration_sql_contains_reset_token_column():
    from scripts.db_migrate import MIGRATION_SQL
    assert "reset_token" in MIGRATION_SQL


def test_migration_sql_contains_reset_token_expires_at_column():
    from scripts.db_migrate import MIGRATION_SQL
    assert "reset_token_expires_at" in MIGRATION_SQL


def test_migration_sql_contains_password_changed_at_column():
    from scripts.db_migrate import MIGRATION_SQL
    assert "password_changed_at" in MIGRATION_SQL


def test_migration_sql_contains_index():
    from scripts.db_migrate import MIGRATION_SQL
    assert "idx_users_reset_token" in MIGRATION_SQL


def test_rollback_sql_contains_drop_columns():
    from scripts.db_migrate import ROLLBACK_SQL
    assert "reset_token" in ROLLBACK_SQL
    assert "password_changed_at" in ROLLBACK_SQL


# --- Config tests ---


def test_config_has_ses_sender_email_property():
    from app.core.config import Config
    assert hasattr(Config, "ses_sender_email")


def test_config_has_disable_email_property():
    from app.core.config import Config
    assert hasattr(Config, "disable_email")


def test_config_has_frontend_url_property():
    from app.core.config import Config
    assert hasattr(Config, "frontend_url")


def test_config_has_aws_region_property():
    from app.core.config import Config
    assert hasattr(Config, "aws_region")


def test_config_disable_email_defaults_to_false(monkeypatch):
    import os
    monkeypatch.delenv("DISABLE_EMAIL", raising=False)
    from app.core.config import Config
    c = Config()
    assert c.disable_email is False


def test_config_disable_email_true_when_env_set(monkeypatch):
    monkeypatch.setenv("DISABLE_EMAIL", "true")
    from app.core.config import Config
    c = Config()
    assert c.disable_email is True
