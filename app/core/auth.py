import logging
import os
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

    to_encode.update({"exp": expire})
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
