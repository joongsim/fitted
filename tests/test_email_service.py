"""Tests for app/services/email_service.py."""
import logging
from unittest.mock import MagicMock, patch, PropertyMock
import pytest


def _patch_disable_email(value: bool):
    """Patch config.disable_email property for email service tests."""
    from app.core.config import Config
    return patch.object(Config, "disable_email", new_callable=PropertyMock, return_value=value)


class TestSendPasswordResetEmailDevMode:
    async def test_disable_email_skips_boto3(self):
        with _patch_disable_email(True), patch("boto3.client") as mock_boto:
            from app.services import email_service
            await email_service.send_password_reset_email(
                "user@example.com",
                "https://example.com/reset-password?token=abc123"
            )
        mock_boto.assert_not_called()

    async def test_disable_email_logs_partial_token(self, caplog):
        with _patch_disable_email(True), caplog.at_level(logging.INFO, logger="app.services.email_service"):
            from app.services import email_service
            await email_service.send_password_reset_email(
                "user@example.com",
                "https://example.com/reset-password?token=abcdefgh_rest_of_token"
            )
        assert "abcdefgh" in caplog.text
        # Full token must NOT be logged
        assert "abcdefgh_rest_of_token" not in caplog.text


class TestSendPasswordResetEmailSES:
    async def test_calls_ses_send_email_with_correct_params(self):
        mock_ses = MagicMock()
        with _patch_disable_email(False), patch("boto3.client", return_value=mock_ses):
            from app.services import email_service
            await email_service.send_password_reset_email(
                "target@example.com",
                "https://example.com/reset-password?token=mytoken"
            )

        mock_ses.send_email.assert_called_once()
        call_kwargs = mock_ses.send_email.call_args[1]
        assert call_kwargs["Destination"]["ToAddresses"] == ["target@example.com"]
        assert "mytoken" in call_kwargs["Message"]["Body"]["Text"]["Data"]

    async def test_ses_exception_propagates(self):
        import botocore.exceptions
        mock_ses = MagicMock()
        mock_ses.send_email.side_effect = botocore.exceptions.ClientError(
            {"Error": {"Code": "MessageRejected", "Message": "Email rejected"}},
            "SendEmail"
        )
        with _patch_disable_email(False), patch("boto3.client", return_value=mock_ses):
            from app.services import email_service
            with pytest.raises(botocore.exceptions.ClientError):
                await email_service.send_password_reset_email(
                    "bad@example.com",
                    "https://example.com/reset-password?token=x"
                )
