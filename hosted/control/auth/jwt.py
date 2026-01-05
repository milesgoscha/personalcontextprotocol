"""JWT token generation and verification.

Uses python-jose for JWT handling with HS256 algorithm.
Access tokens are short-lived (15 min), refresh tokens are long-lived (7 days).
"""

from datetime import datetime, timedelta, UTC
from typing import Any

from jose import jwt, JWTError
from pydantic import BaseModel

from ..config import get_settings


class TokenData(BaseModel):
    """Data extracted from a verified JWT token."""

    user_id: str
    token_type: str  # "access" or "refresh"
    exp: datetime


class TokenError(Exception):
    """Raised when token verification fails."""

    pass


def create_access_token(user_id: str, expires_delta: timedelta | None = None) -> str:
    """Create a short-lived access token.

    Args:
        user_id: The user's UUID.
        expires_delta: Optional custom expiration. Defaults to 15 minutes.

    Returns:
        Encoded JWT string.
    """
    settings = get_settings()
    if expires_delta is None:
        expires_delta = timedelta(minutes=settings.access_token_expire_minutes)

    expire = datetime.now(UTC) + expires_delta
    payload: dict[str, Any] = {
        "sub": user_id,
        "type": "access",
        "exp": expire,
        "iat": datetime.now(UTC),
    }

    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_refresh_token(user_id: str, expires_delta: timedelta | None = None) -> str:
    """Create a long-lived refresh token.

    Args:
        user_id: The user's UUID.
        expires_delta: Optional custom expiration. Defaults to 7 days.

    Returns:
        Encoded JWT string.
    """
    settings = get_settings()
    if expires_delta is None:
        expires_delta = timedelta(days=settings.refresh_token_expire_days)

    expire = datetime.now(UTC) + expires_delta
    payload: dict[str, Any] = {
        "sub": user_id,
        "type": "refresh",
        "exp": expire,
        "iat": datetime.now(UTC),
    }

    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str, expected_type: str | None = None) -> TokenData:
    """Decode and verify a JWT token.

    Args:
        token: The JWT string to decode.
        expected_type: If provided, verify token type matches ("access" or "refresh").

    Returns:
        TokenData with the decoded claims.

    Raises:
        TokenError: If token is invalid, expired, or type doesn't match.
    """
    settings = get_settings()

    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
        )
    except JWTError as e:
        raise TokenError(f"Invalid token: {e}")

    user_id = payload.get("sub")
    token_type = payload.get("type")
    exp = payload.get("exp")

    if not user_id or not token_type or not exp:
        raise TokenError("Token missing required claims")

    if expected_type and token_type != expected_type:
        raise TokenError(f"Expected {expected_type} token, got {token_type}")

    return TokenData(
        user_id=user_id,
        token_type=token_type,
        exp=datetime.fromtimestamp(exp, tz=UTC),
    )


def create_token_pair(user_id: str) -> tuple[str, str]:
    """Create both access and refresh tokens.

    Args:
        user_id: The user's UUID.

    Returns:
        Tuple of (access_token, refresh_token).
    """
    return (
        create_access_token(user_id),
        create_refresh_token(user_id),
    )
