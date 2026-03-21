import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from app.core.auth import get_password_hash
from app.models.user import User, UserCreate
from app.services.db_service import get_connection

logger = logging.getLogger(__name__)


async def create_user(user_in: UserCreate) -> Optional[User]:
    """Create a new user and their default preferences."""
    hashed_password = get_password_hash(user_in.password)

    async with get_connection() as conn:
        async with conn.cursor() as cur:
            try:
                # 1. Insert user
                await cur.execute(
                    """
                    INSERT INTO users (email, hashed_password, full_name)
                    VALUES (%s, %s, %s)
                    RETURNING user_id, email, full_name, is_active, created_at, last_login
                    """,
                    (user_in.email, hashed_password, user_in.full_name),
                )
                user_row = await cur.fetchone()
                if not user_row:
                    logger.warning(
                        "User INSERT returned no row — creation may have silently failed."
                    )
                    return None

                user = User(
                    user_id=user_row[0],
                    email=user_row[1],
                    full_name=user_row[2],
                    is_active=user_row[3],
                    created_at=user_row[4],
                    last_login=user_row[5],
                )

                # 2. Create default preferences
                await cur.execute(
                    "INSERT INTO user_preferences (user_id) VALUES (%s)",
                    (user.user_id,),
                )

                await conn.commit()
                logger.info("New user created: user_id=%s", user.user_id)
                return user
            except Exception:
                await conn.rollback()
                logger.exception(
                    "Failed to create user — transaction rolled back."
                )
                return None


async def get_user_by_email(email: str) -> Optional[Dict[str, Any]]:
    """Get a user by email, including their hashed password for auth."""
    async with get_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT user_id, email, hashed_password, full_name, is_active FROM users WHERE email = %s",
                (email,),
            )
            row = await cur.fetchone()
            if row:
                logger.debug("User lookup by email succeeded: user_id=%s", row[0])
                return {
                    "user_id": row[0],
                    "email": row[1],
                    "hashed_password": row[2],
                    "full_name": row[3],
                    "is_active": row[4],
                }
            logger.debug("User lookup by email found no matching record.")
            return None


async def get_user_by_id(user_id: str) -> Optional[User]:
    """Get a user by their UUID."""
    async with get_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT user_id, email, full_name, is_active, created_at, last_login FROM users WHERE user_id = %s",
                (user_id,),
            )
            row = await cur.fetchone()
            if row:
                return User(
                    user_id=row[0],
                    email=row[1],
                    full_name=row[2],
                    is_active=row[3],
                    created_at=row[4],
                    last_login=row[5],
                )
            logger.warning("User not found: user_id=%s", user_id)
            return None


async def update_last_login(user_id: str) -> None:
    """Update the last_login timestamp for a user."""
    async with get_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE users SET last_login = NOW() WHERE user_id = %s",
                (user_id,),
            )
            await conn.commit()
    logger.debug("Updated last_login for user_id=%s", user_id)


async def get_user_preferences(user_id: str) -> Dict[str, Any]:
    """Get user style and size preferences."""
    async with get_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT style_preferences, size_info FROM user_preferences WHERE user_id = %s",
                (user_id,),
            )
            row = await cur.fetchone()
            if row:
                logger.debug("Preferences fetched for user_id=%s", user_id)
                return {
                    "style_preferences": row[0],
                    "size_info": row[1],
                }
            logger.warning(
                "No preferences row found for user_id=%s — returning empty defaults.",
                user_id,
            )
            return {"style_preferences": {}, "size_info": {}}


async def update_user_preferences(
    user_id: str,
    style_prefs: Optional[Dict] = None,
    size_info: Optional[Dict] = None,
) -> None:
    """Update user style or size preferences."""
    async with get_connection() as conn:
        async with conn.cursor() as cur:
            if style_prefs is not None:
                await cur.execute(
                    "UPDATE user_preferences SET style_preferences = %s, updated_at = NOW() WHERE user_id = %s",
                    (json.dumps(style_prefs), user_id),
                )
            if size_info is not None:
                await cur.execute(
                    "UPDATE user_preferences SET size_info = %s, updated_at = NOW() WHERE user_id = %s",
                    (json.dumps(size_info), user_id),
                )
            await conn.commit()
    logger.info(
        "Preferences updated for user_id=%s (style=%s, size=%s)",
        user_id,
        style_prefs is not None,
        size_info is not None,
    )


async def store_reset_token(email: str, token_hash: str, expires_at: datetime) -> None:
    """
    Store a HMAC-SHA256 reset token hash for a user.
    No-op if email is not found (UPDATE affects 0 rows — caller does not check row count).
    """
    async with get_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE users SET reset_token = %s, reset_token_expires_at = %s WHERE email = %s",
                (token_hash, expires_at, email),
            )
            await conn.commit()
    logger.debug("Stored reset token for email=%s", email)


async def reset_password(
    token_hash: str, new_hashed_password: str, changed_at: datetime
) -> Optional[Dict[str, Any]]:
    """
    Atomically consume a reset token and update the user's password in one transaction.

    The consume step uses UPDATE ... RETURNING to atomically validate and clear the token.
    If the token is not found, expired, or the user is inactive, returns None.
    Both operations run in a single psycopg3 transaction (no half-reset state on crash).

    Args:
        token_hash: HMAC-SHA256 hex digest of the raw token (from hash_reset_token).
        new_hashed_password: bcrypt hash of the new password.
        changed_at: datetime.now(timezone.utc) from the caller — used for password_changed_at.
                    Must use the app clock (not DB NOW()) to match JWT iat clock source.

    Returns:
        Dict with user_id and email if successful, None otherwise.
    """
    async with get_connection() as conn:
        async with conn.cursor() as cur:
            try:
                # Step 1: Atomically consume the token (validate + clear in one statement)
                await cur.execute(
                    """
                    UPDATE users
                    SET reset_token = NULL, reset_token_expires_at = NULL
                    WHERE reset_token = %s
                      AND reset_token_expires_at > NOW()
                      AND is_active = TRUE
                    RETURNING user_id, email
                    """,
                    (token_hash,),
                )
                row = await cur.fetchone()
                if not row:
                    return None

                user_id, email = row[0], row[1]

                # Step 2: Update password and record change timestamp
                await cur.execute(
                    "UPDATE users SET hashed_password = %s, password_changed_at = %s WHERE user_id = %s",
                    (new_hashed_password, changed_at, user_id),
                )
                await conn.commit()
                logger.info("Password reset completed.")
                return {"user_id": user_id, "email": email}
            except Exception:
                await conn.rollback()
                logger.exception("Password reset failed — transaction rolled back.")
                return None
