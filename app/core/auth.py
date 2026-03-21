import calendar
import hashlib
import hmac
import logging
import os
import time
from datetime import datetime, timedelta
from typing import Optional

import bcrypt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt

from app.core.config import config
from app.models.user import TokenData

logger = logging.getLogger(__name__)

# OAuth2 scheme for token extraction
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login", auto_error=False)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plain password against a hashed one."""
    return bcrypt.checkpw(plain_password.encode(), hashed_password.encode())


def get_password_hash(password: str) -> str:
    """Generate a bcrypt hash of a password."""
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def hash_reset_token(raw_token: str) -> str:
    """
    HMAC-SHA256 hash of a reset token keyed with the JWT secret.
    Always returns a 64-character hex string. Never store the raw token.
    """
    return hmac.new(
        config.jwt_secret_key.encode(),
        raw_token.encode(),
        hashlib.sha256,
    ).hexdigest()


async def get_password_changed_at(user_id: str) -> Optional[datetime]:
    """Fetch password_changed_at timestamp for a user. Returns None if never reset."""
    from app.services.db_service import get_connection
    async with get_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT password_changed_at FROM users WHERE user_id = %s",
                (user_id,),
            )
            row = await cur.fetchone()
            if row:
                return row[0]  # May be None if never reset
            return None


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """
    Create a new JWT access token.

    Args:
        data: Claims to embed in the token (must include ``sub``).
        expires_delta: Optional custom TTL; falls back to configured default.

    Returns:
        Encoded JWT string.
    """
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(
            minutes=config.access_token_expire_minutes
        )

    to_encode.update({
        "exp": expire,
        "iat": int(time.time()),   # python-jose does not auto-include iat — must add explicitly
    })
    encoded_jwt = jwt.encode(
        to_encode,
        config.jwt_secret_key,
        algorithm=config.jwt_algorithm,
    )
    logger.debug(
        "Access token created for sub=%s, expires_at=%s",
        data.get("sub"),
        expire.isoformat(),
    )
    return encoded_jwt


async def get_current_user_id(request: Request) -> str:
    """
    Dependency to get the current user_id from JWT in cookie or Authorization header.

    Includes a DEV_MODE bypass that returns a fixed mock UUID.

    Args:
        request: The incoming FastAPI request.

    Returns:
        User ID string extracted from the JWT ``sub`` claim.

    Raises:
        HTTPException 401: If no token is present or the token is invalid/expired.
    """
    # 1. DEV_MODE bypass
    if os.environ.get("DEV_MODE", "false").lower() == "true":
        logger.debug("DEV_MODE active — returning mock user_id.")
        return "00000000-0000-0000-0000-000000000000"

    # 2. Extract token from cookie or Authorization header
    token = request.cookies.get("access_token")
    if not token:
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.split(" ")[1]

    if not token:
        logger.warning(
            "Unauthenticated request — no token in cookie or Authorization header. "
            "path=%s",
            request.url.path,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = jwt.decode(
            token,
            config.jwt_secret_key,
            algorithms=[config.jwt_algorithm],
        )
        user_id: str = payload.get("sub")
        if user_id is None:
            logger.warning(
                "JWT decoded but 'sub' claim is missing. path=%s", request.url.path
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token: missing sub",
            )
        logger.debug("JWT validated for user_id=%s path=%s", user_id, request.url.path)

        # Check if this token was issued before a password reset
        # (rejects all sessions created before the most recent password change)
        token_iat: int = payload.get("iat", 0)
        password_changed_at = await get_password_changed_at(user_id)
        if password_changed_at is not None:
            # Use calendar.timegm() to treat naive datetime as UTC regardless of server timezone.
            # Use strict < so tokens issued at the same second as the change are allowed
            # (the new post-reset login session must not be immediately rejected)
            changed_at_ts = calendar.timegm(password_changed_at.utctimetuple())
            if token_iat < changed_at_ts:
                logger.warning(
                    "Token predates password change for user_id=%s — rejecting.", user_id
                )
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Token invalidated. Please log in again.",
                )

        return user_id
    except JWTError:
        logger.warning(
            "JWT validation failed for path=%s — token may be expired or tampered.",
            request.url.path,
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
