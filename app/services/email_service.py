import logging

import boto3

from app.core.config import config

logger = logging.getLogger(__name__)


async def send_password_reset_email(to_email: str, reset_url: str) -> None:
    """
    Send a password reset email via AWS SES.

    When DISABLE_EMAIL=true (dev/CI), logs a partial token preview instead of sending.
    SES exceptions are intentionally NOT caught — callers must handle them (return 500).

    Args:
        to_email: Recipient email address.
        reset_url: Full reset URL including the raw token query parameter.
    """
    if config.disable_email:
        # Partial token only — CI logs should still be treated as sensitive.
        token_part = reset_url.split("token=")[-1][:8] + "..."
        logger.info("[DEV] Password reset for %s. Token preview: %s", to_email, token_part)
        return

    # boto3 default retry policy (up to 5 retries, exponential backoff) applies.
    # Exceptions propagate — caller returns HTTP 500.
    client = boto3.client("ses", region_name=config.aws_region)
    client.send_email(
        Source=config.ses_sender_email,
        Destination={"ToAddresses": [to_email]},
        Message={
            "Subject": {"Data": "Reset your Fitted password"},
            "Body": {
                "Text": {
                    "Data": (
                        f"Reset your password (valid 1 hour):\n\n{reset_url}\n\n"
                        "If you did not request this, ignore this email."
                    )
                },
                "Html": {
                    "Data": (
                        f"<p><a href='{reset_url}'>Click here</a> to reset your password "
                        f"(valid 1 hour).</p>"
                        f"<p>If you did not request this, ignore this email.</p>"
                    )
                },
            },
        },
    )
