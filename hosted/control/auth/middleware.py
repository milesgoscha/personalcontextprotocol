"""Authentication middleware and dependencies.

Provides FastAPI dependencies for protecting routes and extracting
the current user from JWT tokens.
"""

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models import User
from .jwt import decode_token, TokenError

# HTTP Bearer scheme for Authorization header
_bearer_scheme = HTTPBearer(auto_error=False)

# Cookie name for access token (shared with dashboard.py)
ACCESS_TOKEN_COOKIE = "pcp_access_token"


async def get_current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    """Get the current authenticated user.

    This dependency accepts authentication via:
    1. Authorization header with Bearer token
    2. Cookie with access token (for dashboard HTMX requests)

    Args:
        request: The FastAPI request object (for cookie access).
        credentials: The Bearer token from the Authorization header.
        db: Database session.

    Returns:
        The authenticated User object.

    Raises:
        HTTPException: 401 if no token, invalid token, or user not found.
    """
    # Try Bearer token first
    access_token = None
    if credentials is not None:
        access_token = credentials.credentials
    else:
        # Fall back to cookie
        access_token = request.cookies.get(ACCESS_TOKEN_COOKIE)

    if access_token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        token_data = decode_token(access_token, expected_type="access")
    except TokenError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e),
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Look up the user
    result = await db.execute(select(User).where(User.id == token_data.user_id))
    user = result.scalar_one_or_none()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user


async def get_current_user_optional(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User | None:
    """Get the current user if authenticated, None otherwise.

    This dependency accepts authentication via:
    1. Authorization header with Bearer token
    2. Cookie with access token (for dashboard HTMX requests)

    Args:
        request: The FastAPI request object (for cookie access).
        credentials: The Bearer token from the Authorization header.
        db: Database session.

    Returns:
        The authenticated User object, or None if not authenticated.
    """
    # Try Bearer token first
    access_token = None
    if credentials is not None:
        access_token = credentials.credentials
    else:
        # Fall back to cookie
        access_token = request.cookies.get(ACCESS_TOKEN_COOKIE)

    if access_token is None:
        return None

    try:
        token_data = decode_token(access_token, expected_type="access")
    except TokenError:
        return None

    result = await db.execute(select(User).where(User.id == token_data.user_id))
    return result.scalar_one_or_none()


# Type aliases for cleaner route signatures
CurrentUser = Annotated[User, Depends(get_current_user)]
OptionalUser = Annotated[User | None, Depends(get_current_user_optional)]
