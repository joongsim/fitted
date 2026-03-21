"""Tests for password reset feature."""
import pytest


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


# --- Pydantic model tests ---


def test_forgot_password_request_valid_email():
    from app.models.user import ForgotPasswordRequest
    req = ForgotPasswordRequest(email="user@example.com")
    assert req.email == "user@example.com"


def test_forgot_password_request_rejects_invalid_email():
    from app.models.user import ForgotPasswordRequest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        ForgotPasswordRequest(email="not-an-email")


def test_reset_password_request_valid():
    from app.models.user import ResetPasswordRequest
    req = ResetPasswordRequest(token="a" * 43, new_password="password123")
    assert req.token == "a" * 43
    assert req.new_password == "password123"


def test_reset_password_request_rejects_short_password():
    from app.models.user import ResetPasswordRequest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        ResetPasswordRequest(token="a" * 43, new_password="short")


def test_reset_password_request_rejects_long_password():
    from app.models.user import ResetPasswordRequest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        ResetPasswordRequest(token="a" * 43, new_password="x" * 129)


def test_reset_password_request_rejects_wrong_token_length():
    from app.models.user import ResetPasswordRequest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        ResetPasswordRequest(token="tooshort", new_password="password123")


def test_reset_password_request_accepts_minimum_password_length():
    from app.models.user import ResetPasswordRequest
    req = ResetPasswordRequest(token="a" * 43, new_password="a" * 8)
    assert len(req.new_password) == 8


def test_reset_password_request_accepts_maximum_password_length():
    from app.models.user import ResetPasswordRequest
    req = ResetPasswordRequest(token="a" * 43, new_password="a" * 128)
    assert len(req.new_password) == 128


def test_reset_password_request_rejects_token_too_long():
    from pydantic import ValidationError
    from app.models.user import ResetPasswordRequest
    with pytest.raises(ValidationError):
        ResetPasswordRequest(token="a" * 44, new_password="validpassword123")
