"""Proxy routes to user's PCP node.

These routes proxy requests to the user's node using the stored admin token.
Used by the dashboard to manage grants, tokens, and view audit logs.

In multi-tenant mode, routes to a shared PCP node with X-User-Id header.
In legacy mode, routes to per-user Docker containers.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, HTTPException, status
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.middleware import CurrentUser
from ..config import get_settings
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


async def _get_node_client_context(
    db: AsyncSession,
    user_id: str,
    username: str,
) -> tuple[str, str | None, dict[str, str]]:
    """Get the URL, admin token, and headers for proxying to user's node.

    In multi-tenant mode:
        - Routes to shared node with X-User-Id header
        - No admin token needed (shared node authenticates via header)

    In legacy mode:
        - Routes to per-user Docker container
        - Uses encrypted admin token for authentication

    Returns:
        Tuple of (internal_url, admin_token, extra_headers).

    Raises:
        HTTPException: If node not found or not ready.
    """
    settings = get_settings()

    if settings.multi_tenant:
        # Multi-tenant mode: route to shared node with X-User-Id header
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

        # In multi-tenant mode, the shared node handles auth via X-User-Id
        return settings.shared_node_url, None, {"X-User-Id": user_id}

    else:
        # Legacy mode: per-user Docker containers
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
        except DecryptionError:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to decrypt admin token",
            )

        internal_url = f"http://pcp-{username}:6001"
        return internal_url, admin_token, {}


# --- Grant Routes ---


@router.get("/grants", response_model=list[GrantResponse])
async def get_grants(
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[GrantResponse]:
    """Get all grants from the user's node."""
    internal_url, admin_token, extra_headers = await _get_node_client_context(
        db, current_user.id, current_user.username
    )

    try:
        async with NodeClient(internal_url, admin_token, extra_headers) as client:
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
    internal_url, admin_token, extra_headers = await _get_node_client_context(
        db, current_user.id, current_user.username
    )

    try:
        async with NodeClient(internal_url, admin_token, extra_headers) as client:
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
    internal_url, admin_token, extra_headers = await _get_node_client_context(
        db, current_user.id, current_user.username
    )

    try:
        async with NodeClient(internal_url, admin_token, extra_headers) as client:
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
    internal_url, admin_token, extra_headers = await _get_node_client_context(
        db, current_user.id, current_user.username
    )

    try:
        async with NodeClient(internal_url, admin_token, extra_headers) as client:
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


@router.post("/token", response_class=HTMLResponse)
async def create_token(
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    subject: str = Form(...),
    hours: int = Form(24),
    scopes: list[str] = Form(default=["query:event.*"]),
) -> HTMLResponse:
    """Create a new token for the user's node.

    Accepts form data and returns HTML for HTMX integration.
    """
    internal_url, admin_token, extra_headers = await _get_node_client_context(
        db, current_user.id, current_user.username
    )

    try:
        async with NodeClient(internal_url, admin_token, extra_headers) as client:
            token = await client.create_token(
                subject=subject,
                scopes=scopes,
                hours=hours,
            )
            # Return HTML snippet for HTMX to insert
            return HTMLResponse(
                content=f'''
                <div class="bg-green-50 border border-green-200 rounded-lg p-4">
                    <h4 class="text-sm font-medium text-green-800 mb-2">Token Created Successfully</h4>
                    <p class="text-xs text-green-600 mb-2">Copy this token now - it won't be shown again!</p>
                    <div class="flex items-center gap-2">
                        <code class="flex-1 text-xs bg-white p-2 rounded border border-green-300 break-all select-all">{token}</code>
                        <button type="button"
                                onclick="navigator.clipboard.writeText('{token}'); this.textContent='Copied!'; setTimeout(() => this.textContent='Copy', 2000)"
                                class="px-3 py-1 text-xs font-medium text-green-700 bg-white border border-green-300 rounded hover:bg-green-50">
                            Copy
                        </button>
                    </div>
                </div>
                '''
            )
    except NodeAuthError:
        return HTMLResponse(
            content='<div class="bg-red-50 border border-red-200 rounded-lg p-4 text-red-800">Admin token expired or invalid. Please try logging out and back in.</div>',
            status_code=401,
        )
    except NodeClientError as e:
        return HTMLResponse(
            content=f'<div class="bg-red-50 border border-red-200 rounded-lg p-4 text-red-800">Failed to create token: {e}</div>',
            status_code=502,
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
    internal_url, admin_token, extra_headers = await _get_node_client_context(
        db, current_user.id, current_user.username
    )

    try:
        async with NodeClient(internal_url, admin_token, extra_headers) as client:
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
    internal_url, admin_token, extra_headers = await _get_node_client_context(
        db, current_user.id, current_user.username
    )

    try:
        async with NodeClient(internal_url, admin_token, extra_headers) as client:
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
