"""Authentication middleware and dependencies.

Provides FastAPI dependencies for protecting routes and extracting
the current user from JWT tokens.
"""

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models import User
from .jwt import decode_token, TokenError

# HTTP Bearer scheme for Authorization header
_bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    """Get the current authenticated user.

    This dependency requires a valid access token in the Authorization header.
    Use this for routes that require authentication.

    Args:
        credentials: The Bearer token from the Authorization header.
        db: Database session.

    Returns:
        The authenticated User object.

    Raises:
        HTTPException: 401 if no token, invalid token, or user not found.
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        token_data = decode_token(credentials.credentials, expected_type="access")
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
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User | None:
    """Get the current user if authenticated, None otherwise.

    This dependency does not require authentication. Use this for routes
    that have different behavior for authenticated vs anonymous users.

    Args:
        credentials: The Bearer token from the Authorization header.
        db: Database session.

    Returns:
        The authenticated User object, or None if not authenticated.
    """
    if credentials is None:
        return None

    try:
        token_data = decode_token(credentials.credentials, expected_type="access")
    except TokenError:
        return None

    result = await db.execute(select(User).where(User.id == token_data.user_id))
    return result.scalar_one_or_none()


# Type aliases for cleaner route signatures
CurrentUser = Annotated[User, Depends(get_current_user)]
OptionalUser = Annotated[User | None, Depends(get_current_user_optional)]
