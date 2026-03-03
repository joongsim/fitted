"""Tests for app/core/config.py — SSM Parameter Store config management."""
import os
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_fresh_config():
    """Return a brand-new Config instance (bypasses the module-level singleton)."""
    from app.core.config import Config

    return Config()


# ---------------------------------------------------------------------------
# SSM mode detection
# ---------------------------------------------------------------------------


class TestSsmModeDetection:
    def test_ssm_enabled_when_use_ssm_explicitly_true(self):
        with patch.dict(os.environ, {"USE_SSM": "true"}, clear=False):
            cfg = _make_fresh_config()
            assert cfg._use_ssm is True

    def test_ssm_enabled_when_use_ssm_env_var_true(self):
        env = {"USE_SSM": "true"}
        # Make sure AWS_EXECUTION_ENV is absent
        with patch.dict(os.environ, env, clear=False):
            os.environ.pop("AWS_EXECUTION_ENV", None)
            cfg = _make_fresh_config()
            assert cfg._use_ssm is True

    def test_ssm_disabled_when_neither_env_var_set(self):
        env = {"USE_SSM": "false"}
        with patch.dict(os.environ, env, clear=False):
            os.environ.pop("AWS_EXECUTION_ENV", None)
            cfg = _make_fresh_config()
            assert cfg._use_ssm is False

    def test_ssm_disabled_by_default(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AWS_EXECUTION_ENV", None)
            os.environ.pop("USE_SSM", None)
            cfg = _make_fresh_config()
            assert cfg._use_ssm is False


# ---------------------------------------------------------------------------
# get_parameter — SSM path
# ---------------------------------------------------------------------------


class TestGetParameterSsmMode:
    def _make_ssm_config(self, ssm_return_value):
        """Create a config in SSM mode with a mocked SSM client."""
        cfg = _make_fresh_config()
        cfg._use_ssm = True

        mock_ssm = MagicMock()
        mock_ssm.get_parameter.return_value = {
            "Parameter": {"Value": ssm_return_value}
        }
        cfg._ssm_client = mock_ssm
        return cfg, mock_ssm

    def test_fetches_value_from_ssm(self):
        cfg, mock_ssm = self._make_ssm_config("secret-value")
        result = cfg.get_parameter("/fitted/some-key")
        assert result == "secret-value"

    def test_ssm_called_with_correct_name_and_decryption(self):
        cfg, mock_ssm = self._make_ssm_config("val")
        cfg.get_parameter("/fitted/my-param")
        mock_ssm.get_parameter.assert_called_once_with(
            Name="/fitted/my-param", WithDecryption=True
        )

    def test_ssm_failure_falls_back_to_default(self):
        cfg = _make_fresh_config()
        cfg._use_ssm = True
        mock_ssm = MagicMock()
        mock_ssm.get_parameter.side_effect = Exception("SSM unavailable")
        cfg._ssm_client = mock_ssm

        result = cfg.get_parameter("/fitted/missing-param", default="fallback-default")
        assert result == "fallback-default"

    def test_ssm_failure_with_no_default_re_raises(self):
        cfg = _make_fresh_config()
        cfg._use_ssm = True
        mock_ssm = MagicMock()
        mock_ssm.get_parameter.side_effect = Exception("SSM unavailable")
        cfg._ssm_client = mock_ssm

        with pytest.raises(Exception, match="SSM unavailable"):
            cfg.get_parameter("/fitted/missing-no-default")


# ---------------------------------------------------------------------------
# get_parameter — local env-var fallback
# ---------------------------------------------------------------------------


class TestGetParameterLocalMode:
    def test_converts_ssm_path_to_env_var_name(self):
        cfg = _make_fresh_config()
        cfg._use_ssm = False
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "env-key-value"}):
            result = cfg.get_parameter("/fitted/openrouter-api-key")
        assert result == "env-key-value"

    def test_path_with_hyphens_converted_to_underscores(self):
        cfg = _make_fresh_config()
        cfg._use_ssm = False
        with patch.dict(os.environ, {"DATABASE_URL": "postgres://localhost/db"}):
            result = cfg.get_parameter("/fitted/database-url")
        assert result == "postgres://localhost/db"

    def test_default_returned_when_env_var_missing(self):
        cfg = _make_fresh_config()
        cfg._use_ssm = False
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("NONEXISTENT_PARAM", None)
            result = cfg.get_parameter("/fitted/nonexistent-param", default="my-default")
        assert result == "my-default"

    def test_raises_value_error_when_env_var_and_default_both_missing(self):
        cfg = _make_fresh_config()
        cfg._use_ssm = False
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("NONEXISTENT_PARAM", None)
            with pytest.raises(ValueError, match="not found in SSM or environment"):
                cfg.get_parameter("/fitted/nonexistent-param")


# ---------------------------------------------------------------------------
# Caching behaviour — same parameter fetched twice only calls SSM once
# ---------------------------------------------------------------------------


class TestGetParameterCaching:
    def test_ssm_called_only_once_for_same_parameter(self):
        cfg = _make_fresh_config()
        cfg._use_ssm = True
        mock_ssm = MagicMock()
        mock_ssm.get_parameter.return_value = {"Parameter": {"Value": "cached-val"}}
        cfg._ssm_client = mock_ssm

        cfg.get_parameter("/fitted/cached-key")
        cfg.get_parameter("/fitted/cached-key")

        # lru_cache should prevent the second SSM call
        assert mock_ssm.get_parameter.call_count == 1

    def test_different_parameters_each_call_ssm(self):
        cfg = _make_fresh_config()
        cfg._use_ssm = True
        mock_ssm = MagicMock()
        mock_ssm.get_parameter.return_value = {"Parameter": {"Value": "v"}}
        cfg._ssm_client = mock_ssm

        cfg.get_parameter("/fitted/key-one")
        cfg.get_parameter("/fitted/key-two")

        assert mock_ssm.get_parameter.call_count == 2


# ---------------------------------------------------------------------------
# Property accessors
# ---------------------------------------------------------------------------


class TestPropertyAccessors:
    def _local_config_with_env(self, env_vars: dict):
        cfg = _make_fresh_config()
        cfg._use_ssm = False
        return cfg, env_vars

    def test_openrouter_api_key_property(self):
        cfg, env = self._local_config_with_env({"OPENROUTER_API_KEY": "or-key-123"})
        with patch.dict(os.environ, env):
            assert cfg.openrouter_api_key == "or-key-123"

    def test_weather_api_key_property(self):
        cfg, env = self._local_config_with_env({"WEATHER_API_KEY": "wa-key-456"})
        with patch.dict(os.environ, env):
            assert cfg.weather_api_key == "wa-key-456"

    def test_database_url_property(self):
        cfg, env = self._local_config_with_env({"DATABASE_URL": "postgres://localhost/testdb"})
        with patch.dict(os.environ, env):
            assert cfg.database_url == "postgres://localhost/testdb"

    def test_weather_bucket_name_reads_env_var_directly(self):
        cfg = _make_fresh_config()
        with patch.dict(os.environ, {"WEATHER_BUCKET_NAME": "fitted-bucket-dev"}):
            assert cfg.weather_bucket_name == "fitted-bucket-dev"

    def test_weather_bucket_name_returns_none_when_unset(self):
        cfg = _make_fresh_config()
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("WEATHER_BUCKET_NAME", None)
            assert cfg.weather_bucket_name is None

    def test_jwt_secret_key_property_uses_default(self):
        cfg = _make_fresh_config()
        cfg._use_ssm = False
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("JWT_SECRET_KEY", None)
            assert cfg.jwt_secret_key == "dev-secret-key-change-me-in-prod"

    def test_jwt_algorithm_property_uses_default(self):
        cfg = _make_fresh_config()
        cfg._use_ssm = False
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("JWT_ALGORITHM", None)
            assert cfg.jwt_algorithm == "HS256"

    def test_access_token_expire_minutes_is_integer(self):
        cfg = _make_fresh_config()
        cfg._use_ssm = False
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ACCESS_TOKEN_EXPIRE_MINUTES", None)
            result = cfg.access_token_expire_minutes
            assert isinstance(result, int)
            assert result == 1440

    def test_access_token_expire_minutes_custom_value(self):
        cfg = _make_fresh_config()
        cfg._use_ssm = False
        with patch.dict(os.environ, {"ACCESS_TOKEN_EXPIRE_MINUTES": "60"}):
            assert cfg.access_token_expire_minutes == 60


# ---------------------------------------------------------------------------
# Lazy SSM client initialisation
# ---------------------------------------------------------------------------


class TestSsmClientLazyLoad:
    def test_ssm_client_not_created_until_accessed(self):
        cfg = _make_fresh_config()
        assert cfg._ssm_client is None

    def test_ssm_client_created_on_first_access(self):
        cfg = _make_fresh_config()
        with patch("boto3.client", return_value=MagicMock()) as mock_boto:
            _ = cfg.ssm_client
            mock_boto.assert_called_once_with("ssm", region_name="us-west-1")

    def test_ssm_client_reused_on_subsequent_accesses(self):
        cfg = _make_fresh_config()
        with patch("boto3.client", return_value=MagicMock()) as mock_boto:
            _ = cfg.ssm_client
            _ = cfg.ssm_client
            assert mock_boto.call_count == 1
