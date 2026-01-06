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


class TokenInfo(BaseModel):
    """Token metadata from the node."""

    token_id: str
    subject: str
    scopes: list[str]
    issued_at: str
    expires_at: str
    trust_tier: str


@router.get("/tokens", response_class=HTMLResponse)
async def list_tokens(
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> HTMLResponse:
    """Get all tokens from the user's node. Returns HTML for HTMX."""
    internal_url, admin_token, extra_headers = await _get_node_client_context(
        db, current_user.id, current_user.username
    )

    try:
        async with NodeClient(internal_url, admin_token, extra_headers) as client:
            tokens = await client.list_tokens()

            if not tokens:
                return HTMLResponse(content='''
                    <div class="p-6 text-center text-surface-500">
                        <svg class="w-12 h-12 mx-auto text-surface-300 mb-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M15 7a2 2 0 012 2m4 0a6 6 0 01-7.743 5.743L11 17H9v2H7v2H4a1 1 0 01-1-1v-2.586a1 1 0 01.293-.707l5.964-5.964A6 6 0 1121 9z"/>
                        </svg>
                        <p class="text-sm font-medium">No active tokens</p>
                        <p class="text-xs mt-1">Create a token below to get started</p>
                    </div>
                ''')

            # Build HTML for each token
            rows = []
            for t in tokens:
                # Parse dates
                from datetime import datetime
                try:
                    issued = datetime.fromisoformat(t['issued_at'].replace('Z', '+00:00'))
                    expires = datetime.fromisoformat(t['expires_at'].replace('Z', '+00:00'))
                    issued_str = issued.strftime('%b %d, %Y')
                    expires_str = expires.strftime('%b %d, %Y')
                except:
                    issued_str = t.get('issued_at', 'Unknown')[:10]
                    expires_str = t.get('expires_at', 'Unknown')[:10]

                # Truncate token_id for display
                token_id = t['token_id']
                token_preview = f"pcp_{token_id[:8]}..."

                # Format scopes
                scopes = t.get('scopes', [])
                if len(scopes) > 2:
                    scopes_str = f"{scopes[0]}, {scopes[1]} +{len(scopes)-2} more"
                else:
                    scopes_str = ", ".join(scopes) if scopes else "None"

                row = f'''
                <div class="px-6 py-4 flex items-center justify-between hover:bg-surface-50 transition-colors" id="token-row-{token_id}">
                    <div class="flex-1 min-w-0">
                        <div class="flex items-center gap-3">
                            <div class="flex-shrink-0 w-8 h-8 rounded-lg bg-accent-100 flex items-center justify-center">
                                <svg class="w-4 h-4 text-accent-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 7a2 2 0 012 2m4 0a6 6 0 01-7.743 5.743L11 17H9v2H7v2H4a1 1 0 01-1-1v-2.586a1 1 0 01.293-.707l5.964-5.964A6 6 0 1121 9z"/>
                                </svg>
                            </div>
                            <div class="min-w-0">
                                <p class="text-sm font-medium text-surface-900 truncate">{t['subject']}</p>
                                <p class="text-xs text-surface-400 font-mono">{token_preview}</p>
                            </div>
                        </div>
                    </div>
                    <div class="hidden sm:block flex-shrink-0 w-40 text-left">
                        <p class="text-xs text-surface-500 truncate" title="{scopes_str}">{scopes_str}</p>
                    </div>
                    <div class="hidden md:block flex-shrink-0 w-28 text-left">
                        <p class="text-xs text-surface-500">{issued_str}</p>
                    </div>
                    <div class="flex-shrink-0 w-28 text-left">
                        <p class="text-xs text-surface-500">{expires_str}</p>
                    </div>
                    <div class="flex-shrink-0 ml-4">
                        <button type="button"
                                hx-delete="/api/v1/node/tokens/{token_id}"
                                hx-target="#token-row-{token_id}"
                                hx-swap="outerHTML"
                                hx-confirm="Revoke this token? This action cannot be undone."
                                class="p-1.5 rounded-lg text-surface-400 hover:text-red-600 hover:bg-red-50 transition-colors">
                            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/>
                            </svg>
                        </button>
                    </div>
                </div>
                '''
                rows.append(row)

            # Add header row
            header = '''
            <div class="px-6 py-3 bg-surface-50 border-b border-surface-100 flex items-center text-xs font-medium text-surface-500 uppercase tracking-wider">
                <div class="flex-1 min-w-0">Token</div>
                <div class="hidden sm:block flex-shrink-0 w-40 text-left">Scopes</div>
                <div class="hidden md:block flex-shrink-0 w-28 text-left">Created</div>
                <div class="flex-shrink-0 w-28 text-left">Expires</div>
                <div class="flex-shrink-0 w-10"></div>
            </div>
            '''

            return HTMLResponse(content=header + ''.join(rows))

    except NodeAuthError:
        return HTMLResponse(
            content='<div class="p-6 text-center text-red-600">Session expired. Please refresh the page.</div>',
            status_code=401,
        )
    except NodeClientError as e:
        return HTMLResponse(
            content=f'<div class="p-6 text-center text-red-600">Failed to load tokens: {e}</div>',
            status_code=502,
        )


@router.delete("/tokens/{token_id}", response_class=HTMLResponse)
async def revoke_token(
    token_id: str,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> HTMLResponse:
    """Revoke a token by ID. Returns HTML for HTMX integration."""
    internal_url, admin_token, extra_headers = await _get_node_client_context(
        db, current_user.id, current_user.username
    )

    try:
        async with NodeClient(internal_url, admin_token, extra_headers) as client:
            await client.revoke_token(token_id)
            # Return empty string - HTMX will remove the row via hx-swap="delete"
            return HTMLResponse(content="", status_code=200)
    except NodeAuthError:
        return HTMLResponse(
            content='<div class="text-red-600 text-sm">Session expired. Please refresh.</div>',
            status_code=401,
        )
    except NodeClientError as e:
        return HTMLResponse(
            content=f'<div class="text-red-600 text-sm">Failed to revoke: {e}</div>',
            status_code=400,
        )


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
