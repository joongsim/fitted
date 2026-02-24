"""Tests for app/services/storage_service.py — S3 weather data storage."""
import json
import os
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SAMPLE_WEATHER = {
    "location": {"name": "London"},
    "current": {"temp_c": 12.0},
}

SAMPLE_BUCKET = "fitted-weather-data-test"


def _client_error(code="NoSuchBucket", message="Bucket not found"):
    """Build a minimal botocore ClientError."""
    return ClientError(
        {"Error": {"Code": code, "Message": message}},
        "PutObject",
    )


# ---------------------------------------------------------------------------
# store_raw_weather_data — IS_LOCAL guard
# ---------------------------------------------------------------------------


class TestStoreRawWeatherDataLocalGuard:
    async def test_skips_upload_when_is_local_true_and_not_lambda(self):
        from app.services import storage_service

        mock_s3 = MagicMock()
        with patch.object(storage_service, "IS_LOCAL", True):
            with patch.object(storage_service, "s3_client", mock_s3):
                with patch.dict(os.environ, {}, clear=False):
                    os.environ.pop("AWS_EXECUTION_ENV", None)
                    await storage_service.store_raw_weather_data("London", SAMPLE_WEATHER)

        mock_s3.put_object.assert_not_called()

    async def test_proceeds_when_is_local_true_but_lambda_env_set(self):
        from app.services import storage_service

        mock_s3 = MagicMock()
        with patch.object(storage_service, "IS_LOCAL", True):
            with patch.object(storage_service, "s3_client", mock_s3):
                with patch.object(storage_service, "WEATHER_BUCKET", SAMPLE_BUCKET):
                    with patch.dict(
                        os.environ,
                        {"AWS_EXECUTION_ENV": "AWS_Lambda_python3.11"},
                    ):
                        await storage_service.store_raw_weather_data("London", SAMPLE_WEATHER)

        mock_s3.put_object.assert_called_once()

    async def test_proceeds_when_is_local_false(self):
        from app.services import storage_service

        mock_s3 = MagicMock()
        with patch.object(storage_service, "IS_LOCAL", False):
            with patch.object(storage_service, "s3_client", mock_s3):
                with patch.object(storage_service, "WEATHER_BUCKET", SAMPLE_BUCKET):
                    with patch.dict(os.environ, {}, clear=False):
                        os.environ.pop("AWS_EXECUTION_ENV", None)
                        await storage_service.store_raw_weather_data("London", SAMPLE_WEATHER)

        mock_s3.put_object.assert_called_once()


# ---------------------------------------------------------------------------
# store_raw_weather_data — missing S3 client / bucket guards
# ---------------------------------------------------------------------------


class TestStoreRawWeatherDataPreConditions:
    async def test_skips_when_s3_client_is_none(self):
        from app.services import storage_service

        with patch.object(storage_service, "IS_LOCAL", False):
            with patch.object(storage_service, "s3_client", None):
                # Should not raise
                await storage_service.store_raw_weather_data("London", SAMPLE_WEATHER)

    async def test_skips_when_weather_bucket_is_none(self):
        from app.services import storage_service

        mock_s3 = MagicMock()
        with patch.object(storage_service, "IS_LOCAL", False):
            with patch.object(storage_service, "s3_client", mock_s3):
                with patch.object(storage_service, "WEATHER_BUCKET", None):
                    await storage_service.store_raw_weather_data("London", SAMPLE_WEATHER)

        mock_s3.put_object.assert_not_called()


# ---------------------------------------------------------------------------
# store_raw_weather_data — S3 key format
# ---------------------------------------------------------------------------


class TestStoreRawWeatherDataS3Key:
    async def test_current_weather_uses_current_prefix(self):
        from app.services import storage_service

        mock_s3 = MagicMock()
        with patch.object(storage_service, "IS_LOCAL", False):
            with patch.object(storage_service, "s3_client", mock_s3):
                with patch.object(storage_service, "WEATHER_BUCKET", SAMPLE_BUCKET):
                    with patch.dict(os.environ, {}, clear=False):
                        os.environ.pop("AWS_EXECUTION_ENV", None)
                        await storage_service.store_raw_weather_data(
                            "London", SAMPLE_WEATHER, is_forecast=False
                        )

        call_kwargs = mock_s3.put_object.call_args[1]
        assert "/current/" in call_kwargs["Key"]

    async def test_forecast_weather_uses_forecast_prefix(self):
        from app.services import storage_service

        mock_s3 = MagicMock()
        with patch.object(storage_service, "IS_LOCAL", False):
            with patch.object(storage_service, "s3_client", mock_s3):
                with patch.object(storage_service, "WEATHER_BUCKET", SAMPLE_BUCKET):
                    with patch.dict(os.environ, {}, clear=False):
                        os.environ.pop("AWS_EXECUTION_ENV", None)
                        await storage_service.store_raw_weather_data(
                            "London", SAMPLE_WEATHER, is_forecast=True
                        )

        call_kwargs = mock_s3.put_object.call_args[1]
        assert "/forecast/" in call_kwargs["Key"]

    async def test_key_contains_date_partition(self):
        from app.services import storage_service
        from datetime import datetime, timezone

        mock_s3 = MagicMock()
        fixed_date = "2025-06-15"

        with patch.object(storage_service, "IS_LOCAL", False):
            with patch.object(storage_service, "s3_client", mock_s3):
                with patch.object(storage_service, "WEATHER_BUCKET", SAMPLE_BUCKET):
                    with patch.dict(os.environ, {}, clear=False):
                        os.environ.pop("AWS_EXECUTION_ENV", None)
                        with patch(
                            "app.services.storage_service.datetime"
                        ) as mock_dt:
                            mock_now = MagicMock()
                            mock_now.strftime.side_effect = lambda fmt: (
                                fixed_date if "Y" in fmt else "12-00-00"
                            )
                            mock_dt.now.return_value = mock_now
                            await storage_service.store_raw_weather_data(
                                "London", SAMPLE_WEATHER
                            )

        call_kwargs = mock_s3.put_object.call_args[1]
        assert f"dt={fixed_date}" in call_kwargs["Key"]

    async def test_location_sanitized_in_key(self):
        from app.services import storage_service

        mock_s3 = MagicMock()
        with patch.object(storage_service, "IS_LOCAL", False):
            with patch.object(storage_service, "s3_client", mock_s3):
                with patch.object(storage_service, "WEATHER_BUCKET", SAMPLE_BUCKET):
                    with patch.dict(os.environ, {}, clear=False):
                        os.environ.pop("AWS_EXECUTION_ENV", None)
                        await storage_service.store_raw_weather_data(
                            "New York City!", SAMPLE_WEATHER
                        )

        call_kwargs = mock_s3.put_object.call_args[1]
        # Special characters stripped; spaces not allowed in S3 key component
        assert "New York City!" not in call_kwargs["Key"]
        assert "newyorkcity" in call_kwargs["Key"]

    async def test_uploaded_to_correct_bucket(self):
        from app.services import storage_service

        mock_s3 = MagicMock()
        with patch.object(storage_service, "IS_LOCAL", False):
            with patch.object(storage_service, "s3_client", mock_s3):
                with patch.object(storage_service, "WEATHER_BUCKET", SAMPLE_BUCKET):
                    with patch.dict(os.environ, {}, clear=False):
                        os.environ.pop("AWS_EXECUTION_ENV", None)
                        await storage_service.store_raw_weather_data("London", SAMPLE_WEATHER)

        call_kwargs = mock_s3.put_object.call_args[1]
        assert call_kwargs["Bucket"] == SAMPLE_BUCKET

    async def test_body_is_json_encoded(self):
        from app.services import storage_service

        mock_s3 = MagicMock()
        with patch.object(storage_service, "IS_LOCAL", False):
            with patch.object(storage_service, "s3_client", mock_s3):
                with patch.object(storage_service, "WEATHER_BUCKET", SAMPLE_BUCKET):
                    with patch.dict(os.environ, {}, clear=False):
                        os.environ.pop("AWS_EXECUTION_ENV", None)
                        await storage_service.store_raw_weather_data("London", SAMPLE_WEATHER)

        call_kwargs = mock_s3.put_object.call_args[1]
        parsed = json.loads(call_kwargs["Body"])
        assert parsed == SAMPLE_WEATHER

    async def test_content_type_is_application_json(self):
        from app.services import storage_service

        mock_s3 = MagicMock()
        with patch.object(storage_service, "IS_LOCAL", False):
            with patch.object(storage_service, "s3_client", mock_s3):
                with patch.object(storage_service, "WEATHER_BUCKET", SAMPLE_BUCKET):
                    with patch.dict(os.environ, {}, clear=False):
                        os.environ.pop("AWS_EXECUTION_ENV", None)
                        await storage_service.store_raw_weather_data("London", SAMPLE_WEATHER)

        call_kwargs = mock_s3.put_object.call_args[1]
        assert call_kwargs["ContentType"] == "application/json"


# ---------------------------------------------------------------------------
# store_raw_weather_data — error handling
# ---------------------------------------------------------------------------


class TestStoreRawWeatherDataErrorHandling:
    async def test_client_error_does_not_raise(self):
        from app.services import storage_service

        mock_s3 = MagicMock()
        mock_s3.put_object.side_effect = _client_error()

        with patch.object(storage_service, "IS_LOCAL", False):
            with patch.object(storage_service, "s3_client", mock_s3):
                with patch.object(storage_service, "WEATHER_BUCKET", SAMPLE_BUCKET):
                    with patch.dict(os.environ, {}, clear=False):
                        os.environ.pop("AWS_EXECUTION_ENV", None)
                        # Should swallow the error and return None
                        result = await storage_service.store_raw_weather_data(
                            "London", SAMPLE_WEATHER
                        )

        assert result is None

    async def test_generic_exception_does_not_raise(self):
        from app.services import storage_service

        mock_s3 = MagicMock()
        mock_s3.put_object.side_effect = RuntimeError("unexpected")

        with patch.object(storage_service, "IS_LOCAL", False):
            with patch.object(storage_service, "s3_client", mock_s3):
                with patch.object(storage_service, "WEATHER_BUCKET", SAMPLE_BUCKET):
                    with patch.dict(os.environ, {}, clear=False):
                        os.environ.pop("AWS_EXECUTION_ENV", None)
                        result = await storage_service.store_raw_weather_data(
                            "London", SAMPLE_WEATHER
                        )

        assert result is None
