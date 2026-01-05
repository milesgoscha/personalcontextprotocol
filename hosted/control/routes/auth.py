"""Authentication routes: signup, login, logout, refresh.

All auth endpoints are under /api/v1/auth/
"""

import hashlib
import re
import secrets
from datetime import datetime, timedelta, UTC
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, field_validator
from sqlalchemy import select, delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.jwt import (
    create_access_token,
    create_refresh_token,
    create_token_pair,
    decode_token,
    TokenError,
)
from ..auth.middleware import CurrentUser
from ..auth.password import hash_password, verify_password
from ..config import get_settings
from ..database import get_db
from ..models import User, Session, AuditLog, Node
from ..services.provisioner import get_provisioner, ProvisioningError

router = APIRouter()


# --- Request/Response Models ---


class SignupRequest(BaseModel):
    """Request body for user signup."""

    email: EmailStr
    username: str
    password: str

    @field_validator("username")
    @classmethod
    def validate_username(cls, v: str) -> str:
        """Validate username is DNS-safe for subdomain routing."""
        v = v.lower().strip()
        if len(v) < 3:
            raise ValueError("Username must be at least 3 characters")
        if len(v) > 63:
            raise ValueError("Username must be at most 63 characters")
        if not re.match(r"^[a-z][a-z0-9-]*[a-z0-9]$", v) and len(v) > 2:
            raise ValueError(
                "Username must start with a letter, contain only lowercase letters, "
                "numbers, and hyphens, and end with a letter or number"
            )
        if "--" in v:
            raise ValueError("Username cannot contain consecutive hyphens")
        return v

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        """Validate password strength."""
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        if len(v) > 128:
            raise ValueError("Password must be at most 128 characters")
        return v


class LoginRequest(BaseModel):
    """Request body for user login."""

    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    """Request body for token refresh."""

    refresh_token: str


class TokenResponse(BaseModel):
    """Response containing JWT tokens."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # Access token expiration in seconds


class UserResponse(BaseModel):
    """Response containing user info."""

    id: str
    email: str
    username: str
    email_verified: bool
    created_at: datetime


class AuthResponse(BaseModel):
    """Response for successful authentication."""

    user: UserResponse
    tokens: TokenResponse


# --- Helper Functions ---


def _hash_refresh_token(token: str) -> str:
    """Hash a refresh token for storage."""
    return hashlib.sha256(token.encode()).hexdigest()


async def _create_session(
    db: AsyncSession,
    user_id: str,
    refresh_token: str,
) -> None:
    """Create a new session with the refresh token hash."""
    settings = get_settings()
    expires_at = datetime.now(UTC) + timedelta(days=settings.refresh_token_expire_days)

    session = Session(
        id=str(uuid4()),
        user_id=user_id,
        refresh_token_hash=_hash_refresh_token(refresh_token),
        expires_at=expires_at,
    )
    db.add(session)


async def _log_audit(
    db: AsyncSession,
    user_id: str | None,
    action: str,
    details: str | None = None,
    ip_address: str | None = None,
) -> None:
    """Create an audit log entry."""
    log = AuditLog(
        id=str(uuid4()),
        user_id=user_id,
        action=action,
        details=details,
        ip_address=ip_address,
    )
    db.add(log)


# --- Routes ---


@router.post("/signup", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
async def signup(
    request: SignupRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AuthResponse:
    """Create a new user account.

    Returns JWT tokens on successful signup. The user's node will be
    provisioned asynchronously.
    """
    settings = get_settings()

    # Create user
    user_id = str(uuid4())
    user = User(
        id=user_id,
        email=request.email.lower(),
        username=request.username,
        password_hash=hash_password(request.password),
    )

    try:
        db.add(user)
        await db.flush()  # Check for constraint violations
    except IntegrityError as e:
        await db.rollback()
        error_str = str(e.orig).lower() if e.orig else ""
        if "email" in error_str:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Email already registered",
            )
        elif "username" in error_str:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Username already taken",
            )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User already exists",
        )

    # Create tokens
    access_token, refresh_token = create_token_pair(user_id)

    # Store session
    await _create_session(db, user_id, refresh_token)

    # Audit log
    await _log_audit(db, user_id, "signup", f"User {request.username} signed up")

    return AuthResponse(
        user=UserResponse(
            id=user.id,
            email=user.email,
            username=user.username,
            email_verified=user.email_verified,
            created_at=user.created_at,
        ),
        tokens=TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=settings.access_token_expire_minutes * 60,
        ),
    )


@router.post("/login", response_model=AuthResponse)
async def login(
    request: LoginRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AuthResponse:
    """Authenticate with email and password.

    Returns JWT tokens on successful login.
    """
    settings = get_settings()

    # Find user by email
    result = await db.execute(
        select(User).where(User.email == request.email.lower())
    )
    user = result.scalar_one_or_none()

    if user is None or not verify_password(request.password, user.password_hash):
        # Use same error for both cases to prevent email enumeration
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    # Create tokens
    access_token, refresh_token = create_token_pair(user.id)

    # Store session
    await _create_session(db, user.id, refresh_token)

    # Audit log
    await _log_audit(db, user.id, "login", f"User {user.username} logged in")

    return AuthResponse(
        user=UserResponse(
            id=user.id,
            email=user.email,
            username=user.username,
            email_verified=user.email_verified,
            created_at=user.created_at,
        ),
        tokens=TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=settings.access_token_expire_minutes * 60,
        ),
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    request: RefreshRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TokenResponse:
    """Get a new access token using a refresh token.

    The refresh token is rotated (old one invalidated, new one issued).
    """
    settings = get_settings()

    # Decode the refresh token
    try:
        token_data = decode_token(request.refresh_token, expected_type="refresh")
    except TokenError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e),
        )

    # Verify session exists with this refresh token
    token_hash = _hash_refresh_token(request.refresh_token)
    result = await db.execute(
        select(Session).where(
            Session.user_id == token_data.user_id,
            Session.refresh_token_hash == token_hash,
            Session.expires_at > datetime.now(UTC),
        )
    )
    session = result.scalar_one_or_none()

    if session is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )

    # Delete old session (token rotation)
    await db.delete(session)

    # Create new tokens
    access_token, new_refresh_token = create_token_pair(token_data.user_id)

    # Store new session
    await _create_session(db, token_data.user_id, new_refresh_token)

    return TokenResponse(
        access_token=access_token,
        refresh_token=new_refresh_token,
        expires_in=settings.access_token_expire_minutes * 60,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    """Log out the current user.

    Invalidates all sessions for this user.
    """
    # Delete all sessions for this user
    await db.execute(delete(Session).where(Session.user_id == current_user.id))

    # Audit log
    await _log_audit(
        db, current_user.id, "logout", f"User {current_user.username} logged out"
    )


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: CurrentUser) -> UserResponse:
    """Get the current user's profile."""
    return UserResponse(
        id=current_user.id,
        email=current_user.email,
        username=current_user.username,
        email_verified=current_user.email_verified,
        created_at=current_user.created_at,
    )


class DeleteAccountRequest(BaseModel):
    """Request body for account deletion."""

    password: str


@router.delete("/account", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(
    request: DeleteAccountRequest,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    """Permanently delete the user's account and all data.

    This action:
    1. Verifies password
    2. Stops and removes the user's PCP node container
    3. Deletes the user's data volume
    4. Deletes all database records (sessions, node, audit logs, user)

    This action is irreversible.
    """
    # Verify password
    if not verify_password(request.password, current_user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid password",
        )

    # Get the user's node
    result = await db.execute(select(Node).where(Node.user_id == current_user.id))
    node = result.scalar_one_or_none()

    # Clean up Docker resources if node exists
    if node:
        try:
            provisioner = get_provisioner()
            await provisioner.cleanup_user(current_user.username)
        except ProvisioningError as e:
            # Log but continue - we still want to delete the account
            await _log_audit(
                db,
                current_user.id,
                "account_delete_warning",
                f"Failed to clean up Docker resources: {e}",
            )

        # Delete the node record
        await db.delete(node)

    # Delete all sessions (revokes all tokens)
    await db.execute(delete(Session).where(Session.user_id == current_user.id))

    # Delete audit logs for this user
    await db.execute(delete(AuditLog).where(AuditLog.user_id == current_user.id))

    # Log the deletion (with user_id=None since user will be deleted)
    final_log = AuditLog(
        id=str(uuid4()),
        user_id=None,
        action="account_deleted",
        details=f"User {current_user.username} ({current_user.email}) deleted their account",
    )
    db.add(final_log)

    # Delete the user
    await db.delete(current_user)

    await db.commit()
