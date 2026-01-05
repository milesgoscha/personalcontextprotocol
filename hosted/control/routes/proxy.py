"""Proxy routes to user's PCP node.

These routes proxy requests to the user's node using the stored admin token.
Used by the dashboard to manage grants, tokens, and view audit logs.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.middleware import CurrentUser
from ..database import get_db
from ..models import Node, NodeStatus
from ..services.encryption import decrypt_token, DecryptionError
from ..services.node_client import NodeClient, NodeClientError, NodeAuthError

router = APIRouter()


# --- Request/Response Models ---


class GrantResponse(BaseModel):
    """Grant object from the node."""

    id: str
    requester: str
    scopes: list[str]
    status: str
    requested_at: str
    resolved_at: str | None = None


class CreateTokenRequest(BaseModel):
    """Request to create a new token."""

    subject: str
    scopes: list[str]
    hours: int = 24


class CreateTokenResponse(BaseModel):
    """Response with the created token."""

    token: str


class AuditLogEntry(BaseModel):
    """Audit log entry from the node."""

    timestamp: str
    action: str
    subject: str | None = None
    details: dict[str, Any] | None = None


# --- Helper Functions ---


async def _get_node_client(
    db: AsyncSession,
    user_id: str,
    username: str,
) -> tuple[Node, str]:
    """Get the user's node and decrypted admin token.

    Returns:
        Tuple of (node, admin_token).

    Raises:
        HTTPException: If node not found, not running, or token decryption fails.
    """
    result = await db.execute(select(Node).where(Node.user_id == user_id))
    node = result.scalar_one_or_none()

    if not node:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No node found",
        )

    if node.status != NodeStatus.RUNNING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Node is not running (status: {node.status.value})",
        )

    if not node.admin_token_encrypted:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Node admin token not available",
        )

    try:
        admin_token = decrypt_token(
            node.admin_token_encrypted,
            user_id,
            node.admin_token_version,
        )
    except DecryptionError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to decrypt admin token",
        )

    return node, admin_token


def _get_internal_url(username: str) -> str:
    """Get the internal Docker network URL for a user's node."""
    return f"http://pcp-{username}:9315"


# --- Grant Routes ---


@router.get("/grants", response_model=list[GrantResponse])
async def get_grants(
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[GrantResponse]:
    """Get all grants from the user's node."""
    node, admin_token = await _get_node_client(db, current_user.id, current_user.username)
    internal_url = _get_internal_url(current_user.username)

    try:
        async with NodeClient(internal_url, admin_token) as client:
            grants = await client.get_grants()
            return [GrantResponse(**g) for g in grants]
    except NodeAuthError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Admin token expired or invalid",
        )
    except NodeClientError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(e),
        )


@router.post("/grants/{grant_id}/approve", response_model=GrantResponse)
async def approve_grant(
    grant_id: str,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> GrantResponse:
    """Approve a pending grant."""
    node, admin_token = await _get_node_client(db, current_user.id, current_user.username)
    internal_url = _get_internal_url(current_user.username)

    try:
        async with NodeClient(internal_url, admin_token) as client:
            grant = await client.approve_grant(grant_id)
            return GrantResponse(**grant)
    except NodeAuthError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Admin token expired or invalid",
        )
    except NodeClientError as e:
        if "not found" in str(e).lower():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Grant {grant_id} not found",
            )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(e),
        )


@router.post("/grants/{grant_id}/deny", response_model=GrantResponse)
async def deny_grant(
    grant_id: str,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> GrantResponse:
    """Deny a pending grant."""
    node, admin_token = await _get_node_client(db, current_user.id, current_user.username)
    internal_url = _get_internal_url(current_user.username)

    try:
        async with NodeClient(internal_url, admin_token) as client:
            grant = await client.deny_grant(grant_id)
            return GrantResponse(**grant)
    except NodeAuthError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Admin token expired or invalid",
        )
    except NodeClientError as e:
        if "not found" in str(e).lower():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Grant {grant_id} not found",
            )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(e),
        )


@router.post("/grants/{grant_id}/revoke", response_model=GrantResponse)
async def revoke_grant(
    grant_id: str,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> GrantResponse:
    """Revoke an active grant."""
    node, admin_token = await _get_node_client(db, current_user.id, current_user.username)
    internal_url = _get_internal_url(current_user.username)

    try:
        async with NodeClient(internal_url, admin_token) as client:
            grant = await client.revoke_grant(grant_id)
            return GrantResponse(**grant)
    except NodeAuthError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Admin token expired or invalid",
        )
    except NodeClientError as e:
        if "not found" in str(e).lower():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Grant {grant_id} not found",
            )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(e),
        )


# --- Token Routes ---


@router.post("/token", response_model=CreateTokenResponse)
async def create_token(
    request: CreateTokenRequest,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> CreateTokenResponse:
    """Create a new token for the user's node."""
    node, admin_token = await _get_node_client(db, current_user.id, current_user.username)
    internal_url = _get_internal_url(current_user.username)

    try:
        async with NodeClient(internal_url, admin_token) as client:
            token = await client.create_token(
                subject=request.subject,
                scopes=request.scopes,
                hours=request.hours,
            )
            return CreateTokenResponse(token=token)
    except NodeAuthError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Admin token expired or invalid",
        )
    except NodeClientError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(e),
        )


# --- Audit Routes ---


@router.get("/audit", response_model=list[AuditLogEntry])
async def get_audit_log(
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: int = 100,
    offset: int = 0,
) -> list[AuditLogEntry]:
    """Get audit log entries from the user's node."""
    node, admin_token = await _get_node_client(db, current_user.id, current_user.username)
    internal_url = _get_internal_url(current_user.username)

    try:
        async with NodeClient(internal_url, admin_token) as client:
            entries = await client.get_audit_log(limit=limit, offset=offset)
            return [AuditLogEntry(**e) for e in entries]
    except NodeAuthError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Admin token expired or invalid",
        )
    except NodeClientError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(e),
        )


# --- Export Routes ---


@router.get("/export")
async def export_data(
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> StreamingResponse:
    """Export all data from the user's node as JSONL."""
    node, admin_token = await _get_node_client(db, current_user.id, current_user.username)
    internal_url = _get_internal_url(current_user.username)

    try:
        async with NodeClient(internal_url, admin_token) as client:
            data = await client.export_data()

            return StreamingResponse(
                iter([data]),
                media_type="application/x-ndjson",
                headers={
                    "Content-Disposition": f"attachment; filename={current_user.username}-export.jsonl"
                },
            )
    except NodeAuthError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Admin token expired or invalid",
        )
    except NodeClientError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(e),
        )
