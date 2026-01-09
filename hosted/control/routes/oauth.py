"""OAuth 2.1 routes for MCP client authentication.

Implements OAuth 2.1 with PKCE for MCP clients like Claude Code to authenticate
with user PCP nodes without manual Bearer token configuration.

Endpoints:
- GET /.well-known/oauth-authorization-server - Authorization server metadata
- POST /oauth/register - Dynamic client registration
- GET /oauth/authorize - Authorization endpoint
- POST /oauth/authorize/consent - Handle user consent
- POST /oauth/token - Token endpoint
"""

import hashlib
import html
import json
import secrets
from base64 import urlsafe_b64decode
from datetime import datetime, timedelta, UTC
from typing import Annotated
from urllib.parse import urlencode, urlparse

# Scopes that OAuth clients are allowed to request (third-party allowlist)
# This prevents malicious clients from requesting admin scopes
ALLOWED_OAUTH_SCOPES = {
    # Read scopes (safe for third-party apps)
    "query:event.*",
    "query:event.summary",
    "query:learning.*",
    "query:reflection.*",
    "query:identity",
    # Write scopes (require explicit consent)
    "observe:event",
    "learn:write",
    "reflect:write",
}

# Default scopes when client doesn't specify any (read-only for third-party trust tier)
DEFAULT_OAUTH_SCOPES = "query:event.summary query:learning.* query:reflection.* query:identity"

# Scope sets for user-selected access levels on consent page
READ_ONLY_SCOPES = "query:event.* query:learning.* query:reflection.* query:identity"
FULL_ACCESS_SCOPES = "query:event.* query:learning.* query:reflection.* query:identity observe:event learn:write reflect:write"

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.jwt import decode_token, TokenError
from ..auth.middleware import get_current_user_optional
from ..config import get_settings
from ..database import get_db

# Initialize templates
import os
_templates_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")
templates = Jinja2Templates(directory=_templates_dir)
from ..models import (
    Node,
    NodeStatus,
    OAuthAuthorizationCode,
    OAuthClient,
    OAuthRefreshToken,
    User,
)
from ..services.encryption import decrypt_token, DecryptionError
from ..services.node_client import NodeClient, NodeClientError

router = APIRouter()


# --- Helper Functions ---


def _hash_code(code: str) -> str:
    """Hash an authorization code or refresh token for storage."""
    return hashlib.sha256(code.encode()).hexdigest()


def _validate_and_filter_scopes(scope: str) -> str:
    """Validate and filter requested scopes against the allowlist.

    Returns only the scopes that are in ALLOWED_OAUTH_SCOPES.
    If no valid scopes remain, returns DEFAULT_OAUTH_SCOPES.
    """
    if not scope:
        return DEFAULT_OAUTH_SCOPES

    requested = scope.split()
    valid_scopes = [s for s in requested if s in ALLOWED_OAUTH_SCOPES]

    if not valid_scopes:
        return DEFAULT_OAUTH_SCOPES

    return " ".join(valid_scopes)


def _verify_pkce(code_verifier: str, code_challenge: str) -> bool:
    """Verify PKCE S256 challenge."""
    # S256: BASE64URL(SHA256(code_verifier)) == code_challenge
    digest = hashlib.sha256(code_verifier.encode()).digest()
    # URL-safe base64 encode without padding
    computed = (
        digest.hex()
        if len(code_challenge) == 64
        else urlsafe_b64decode(code_challenge + "==").hex()
    )
    # For S256, code_challenge is base64url(sha256(verifier))
    verifier_hash = hashlib.sha256(code_verifier.encode()).digest()
    import base64

    expected = base64.urlsafe_b64encode(verifier_hash).rstrip(b"=").decode()
    return expected == code_challenge


async def _get_node_client_context(
    db: AsyncSession,
    user_id: str,
    username: str,
) -> tuple[str, str | None, dict[str, str]]:
    """Get the URL, admin token, and headers for proxying to user's node.

    Copied from proxy.py for OAuth token endpoint use.
    """
    settings = get_settings()

    if settings.multi_tenant:
        # Multi-tenant mode: route to shared node with X-User-Id header
        result = await db.execute(select(Node).where(Node.user_id == user_id))
        node = result.scalar_one_or_none()

        if not node:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No node found for user",
            )

        if node.status != NodeStatus.RUNNING:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Node is not running (status: {node.status.value})",
            )

        return settings.shared_node_url, None, {"X-User-Id": user_id}

    else:
        # Legacy mode: per-user Docker containers
        result = await db.execute(select(Node).where(Node.user_id == user_id))
        node = result.scalar_one_or_none()

        if not node:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No node found for user",
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


# --- Discovery Endpoints ---


@router.get("/.well-known/oauth-authorization-server")
async def oauth_authorization_server_metadata(request: Request):
    """Return OAuth 2.1 Authorization Server Metadata (RFC 8414)."""
    settings = get_settings()
    # Use X-Forwarded-Proto header (set by Traefik) or fall back to request scheme
    scheme = request.headers.get("X-Forwarded-Proto", request.url.scheme) or "https"
    base_url = f"{scheme}://{settings.pcp_domain}"

    return {
        "issuer": base_url,
        "authorization_endpoint": f"{base_url}/oauth/authorize",
        "token_endpoint": f"{base_url}/oauth/token",
        "registration_endpoint": f"{base_url}/oauth/register",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
        "scopes_supported": [
            "query:event.*",
            "query:learning.*",
            "query:reflection.*",
            "query:identity",
            "observe:event",
            "learn:write",
            "reflect:write",
        ],
    }


# --- Dynamic Client Registration ---


class ClientRegistrationRequest(BaseModel):
    """OAuth 2.1 Dynamic Client Registration request."""

    client_name: str
    redirect_uris: list[str]


class ClientRegistrationResponse(BaseModel):
    """OAuth 2.1 Dynamic Client Registration response."""

    client_id: str
    client_name: str
    redirect_uris: list[str]


@router.post("/oauth/register")
async def register_client(
    request: ClientRegistrationRequest,
    db: AsyncSession = Depends(get_db),
) -> ClientRegistrationResponse:
    """Register a new OAuth client (RFC 7591).

    No client_secret is issued - PKCE is required for all clients.
    """
    # Generate client_id
    client_id = f"pcp_client_{secrets.token_hex(16)}"

    # Store client
    client = OAuthClient(
        client_id=client_id,
        client_name=request.client_name,
        redirect_uris=json.dumps(request.redirect_uris),
    )
    db.add(client)
    await db.commit()

    return ClientRegistrationResponse(
        client_id=client_id,
        client_name=request.client_name,
        redirect_uris=request.redirect_uris,
    )


# --- Authorization Endpoint ---


@router.get("/oauth/authorize")
async def authorize(
    request: Request,
    client_id: str = Query(...),
    redirect_uri: str = Query(...),
    response_type: str = Query(...),
    code_challenge: str = Query(...),
    code_challenge_method: str = Query(...),
    state: str = Query(...),
    resource: str = Query(...),
    scope: str = Query(""),  # Optional - defaults to empty, we'll use a default set
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_current_user_optional),
):
    """OAuth 2.1 Authorization Endpoint.

    Validates the request, ensures user is logged in and owns the resource,
    then shows consent page.
    """
    settings = get_settings()

    # Validate and filter scopes against allowlist (prevents requesting pcp:admin etc.)
    scope = _validate_and_filter_scopes(scope)

    # Validate client_id FIRST - don't redirect if invalid (prevents open redirect)
    result = await db.execute(
        select(OAuthClient).where(OAuthClient.client_id == client_id)
    )
    client = result.scalar_one_or_none()
    if not client:
        # Return error page, NOT redirect (OAuth spec: don't redirect on invalid client)
        return _render_error_page(
            request=request,
            error="invalid_client",
            error_description="The client_id is not registered. Please check your OAuth configuration.",
        )

    # Validate redirect_uri BEFORE any redirects
    allowed_uris = json.loads(client.redirect_uris)
    if redirect_uri not in allowed_uris:
        # Don't redirect to an unregistered URI
        raise HTTPException(
            status_code=400,
            detail="redirect_uri not registered for this client",
        )

    # Now that we've validated client and redirect_uri, we can safely redirect errors

    # Validate response_type
    if response_type != "code":
        return _oauth_error_redirect(
            redirect_uri, "unsupported_response_type", state
        )

    # Validate code_challenge_method
    if code_challenge_method != "S256":
        return _oauth_error_redirect(
            redirect_uri, "invalid_request", state, "Only S256 is supported"
        )

    # Extract username from resource
    # e.g., "https://miles.pcp.sh" -> "miles"
    parsed = urlparse(resource)
    hostname = parsed.netloc or parsed.path
    if not hostname.endswith(f".{settings.pcp_domain}"):
        return _oauth_error_redirect(
            redirect_uri, "invalid_request", state, "Invalid resource"
        )
    username = hostname[: -len(f".{settings.pcp_domain}")]

    # Check if user is logged in
    if not current_user:
        # Redirect to login with return URL
        login_url = f"/login?next=" + urlencode({"": request.url._url})[1:]
        return RedirectResponse(url=login_url, status_code=302)

    # Verify logged-in user matches resource owner
    if current_user.username != username:
        return _oauth_error_redirect(
            redirect_uri,
            "access_denied",
            state,
            "You can only authorize your own resources",
        )

    # Show consent page
    return _render_consent_page(
        request=request,
        client_name=client.client_name,
        scope=scope,
        client_id=client_id,
        redirect_uri=redirect_uri,
        code_challenge=code_challenge,
        state=state,
        username=username,
    )


def _get_app_name_from_redirect(redirect_uri: str) -> str:
    """Determine user-friendly app name from OAuth redirect URI."""
    parsed = urlparse(redirect_uri)
    host = parsed.netloc.lower()

    if "claude.ai" in host:
        return "Claude"
    elif "localhost" in host or "127.0.0.1" in host:
        return "Claude Code"
    elif "anthropic.com" in host:
        return "Claude"
    else:
        # Extract domain name as fallback
        return host.split(".")[0].title() if host else "the app"


def _oauth_error_redirect(
    redirect_uri: str,
    error: str,
    state: str,
    error_description: str | None = None,
) -> RedirectResponse:
    """Redirect with OAuth error parameters."""
    params = {"error": error, "state": state}
    if error_description:
        params["error_description"] = error_description
    return RedirectResponse(
        url=f"{redirect_uri}?{urlencode(params)}", status_code=302
    )


def _render_consent_page(
    request: Request,
    client_name: str,
    scope: str,
    client_id: str,
    redirect_uri: str,
    code_challenge: str,
    state: str,
    username: str,
):
    """Render the OAuth consent page using Jinja2 template.

    Jinja2 auto-escapes all values by default, preventing XSS attacks.
    """
    scopes = scope.split()

    return templates.TemplateResponse(
        "oauth/consent.html",
        {
            "request": request,
            "client_name": client_name,
            "username": username,
            "scopes": scopes,
            "scope": scope,
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "code_challenge": code_challenge,
            "state": state,
        },
    )


def _render_error_page(
    request: Request,
    error: str,
    error_description: str | None = None,
):
    """Render the OAuth error page using Jinja2 template."""
    return templates.TemplateResponse(
        "oauth/error.html",
        {
            "request": request,
            "error": error,
            "error_description": error_description,
        },
        status_code=400,
    )


# --- Consent Handler ---


@router.post("/oauth/authorize/consent")
async def handle_consent(
    request: Request,
    client_id: str = Form(...),
    redirect_uri: str = Form(...),
    scope: str = Form(...),
    code_challenge: str = Form(...),
    state: str = Form(...),
    username: str = Form(...),
    action: str = Form(...),
    access_level: str = Form("full"),  # "read" or "full"
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_current_user_optional),
):
    """Handle user consent decision."""
    if not current_user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    if current_user.username != username:
        raise HTTPException(status_code=403, detail="Cannot authorize for another user")

    if action == "deny":
        return _oauth_error_redirect(redirect_uri, "access_denied", state)

    # Determine final scopes based on user's access level selection
    if access_level == "read":
        final_scope = READ_ONLY_SCOPES
    else:
        final_scope = FULL_ACCESS_SCOPES

    # Generate authorization code
    code = secrets.token_urlsafe(32)
    code_hash = _hash_code(code)

    # Store authorization code (expires in 10 minutes)
    auth_code = OAuthAuthorizationCode(
        code_hash=code_hash,
        client_id=client_id,
        user_id=current_user.id,
        username=username,
        redirect_uri=redirect_uri,
        scope=final_scope,
        code_challenge=code_challenge,
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )
    db.add(auth_code)
    await db.commit()

    # Determine app name from redirect_uri for user-friendly messaging
    app_name = _get_app_name_from_redirect(redirect_uri)

    # Get client name for display
    result = await db.execute(
        select(OAuthClient).where(OAuthClient.client_id == client_id)
    )
    client = result.scalar_one_or_none()
    client_name = client.client_name if client else "the application"

    # Show success interstitial, then redirect
    redirect_url = f"{redirect_uri}?code={code}&state={state}"
    return templates.TemplateResponse(
        "oauth/success.html",
        {
            "request": request,
            "client_name": client_name,
            "app_name": app_name,
            "redirect_url": redirect_url,
        },
    )


# --- Token Endpoint ---


class TokenRequest(BaseModel):
    """OAuth 2.1 Token Request."""

    grant_type: str
    code: str | None = None
    redirect_uri: str | None = None
    code_verifier: str | None = None
    client_id: str | None = None
    refresh_token: str | None = None


class TokenResponse(BaseModel):
    """OAuth 2.1 Token Response."""

    access_token: str
    token_type: str = "Bearer"
    expires_in: int
    refresh_token: str
    scope: str


@router.post("/oauth/token")
async def token_endpoint(
    grant_type: str = Form(...),
    code: str | None = Form(None),
    redirect_uri: str | None = Form(None),
    code_verifier: str | None = Form(None),
    client_id: str | None = Form(None),
    refresh_token: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
):
    """OAuth 2.1 Token Endpoint.

    Supports:
    - authorization_code: Exchange code for tokens
    - refresh_token: Refresh access token
    """
    if grant_type == "authorization_code":
        return await _handle_authorization_code_grant(
            code=code,
            redirect_uri=redirect_uri,
            code_verifier=code_verifier,
            client_id=client_id,
            db=db,
        )
    elif grant_type == "refresh_token":
        return await _handle_refresh_token_grant(
            refresh_token=refresh_token,
            client_id=client_id,
            db=db,
        )
    else:
        return JSONResponse(
            status_code=400,
            content={"error": "unsupported_grant_type"},
        )


async def _handle_authorization_code_grant(
    code: str | None,
    redirect_uri: str | None,
    code_verifier: str | None,
    client_id: str | None,
    db: AsyncSession,
):
    """Handle authorization_code grant type."""
    if not code or not redirect_uri or not code_verifier or not client_id:
        return JSONResponse(
            status_code=400,
            content={"error": "invalid_request", "error_description": "Missing required parameters"},
        )

    # Look up authorization code
    code_hash = _hash_code(code)
    result = await db.execute(
        select(OAuthAuthorizationCode).where(
            OAuthAuthorizationCode.code_hash == code_hash
        )
    )
    auth_code = result.scalar_one_or_none()

    if not auth_code:
        return JSONResponse(
            status_code=400,
            content={"error": "invalid_grant", "error_description": "Invalid or expired authorization code"},
        )

    # Check expiry
    if auth_code.expires_at < datetime.now(UTC):
        await db.execute(
            delete(OAuthAuthorizationCode).where(
                OAuthAuthorizationCode.id == auth_code.id
            )
        )
        await db.commit()
        return JSONResponse(
            status_code=400,
            content={"error": "invalid_grant", "error_description": "Authorization code expired"},
        )

    # Verify client_id
    if auth_code.client_id != client_id:
        return JSONResponse(
            status_code=400,
            content={"error": "invalid_grant", "error_description": "Client ID mismatch"},
        )

    # Verify redirect_uri
    if auth_code.redirect_uri != redirect_uri:
        return JSONResponse(
            status_code=400,
            content={"error": "invalid_grant", "error_description": "Redirect URI mismatch"},
        )

    # Verify PKCE
    if not _verify_pkce(code_verifier, auth_code.code_challenge):
        return JSONResponse(
            status_code=400,
            content={"error": "invalid_grant", "error_description": "Invalid code_verifier"},
        )

    # Delete the code (single-use)
    await db.execute(
        delete(OAuthAuthorizationCode).where(
            OAuthAuthorizationCode.id == auth_code.id
        )
    )

    # Get client name
    result = await db.execute(
        select(OAuthClient).where(OAuthClient.client_id == client_id)
    )
    oauth_client = result.scalar_one_or_none()
    client_name = oauth_client.client_name if oauth_client else "Unknown Client"

    # Create grant and issue token via node
    try:
        node_url, admin_token, extra_headers = await _get_node_client_context(
            db=db,
            user_id=auth_code.user_id,
            username=auth_code.username,
        )

        async with NodeClient(node_url, admin_token, extra_headers) as client:
            # Request grant
            grant_resp = await client.request_grant(
                client_id=f"oauth:{client_id}",
                client_name=client_name,
                scopes_requested=auth_code.scope.split(),
                reason=f"OAuth authorization for {client_name}",
                trust_tier="third_party",
            )

            # Auto-approve (control plane has admin credentials)
            await client.approve_grant(grant_resp["grant_id"])

            # Claim token
            token_resp = await client.claim_grant_token(
                grant_resp["grant_id"],
                grant_resp["claim_secret"],
            )

    except NodeClientError as e:
        return JSONResponse(
            status_code=500,
            content={"error": "server_error", "error_description": str(e)},
        )

    # Generate refresh token
    refresh_token_value = secrets.token_urlsafe(32)
    refresh_token_hash = _hash_code(refresh_token_value)

    # Store refresh token (30 days)
    refresh_token_record = OAuthRefreshToken(
        token_hash=refresh_token_hash,
        client_id=client_id,
        user_id=auth_code.user_id,
        username=auth_code.username,
        scope=auth_code.scope,
        pcp_token_id=token_resp["token_id"],
        pcp_grant_id=grant_resp["grant_id"],
        expires_at=datetime.now(UTC) + timedelta(days=30),
    )
    db.add(refresh_token_record)
    await db.commit()

    return TokenResponse(
        access_token=token_resp["token"],
        expires_in=3600,  # 1 hour
        refresh_token=refresh_token_value,
        scope=auth_code.scope,
    )


async def _handle_refresh_token_grant(
    refresh_token: str | None,
    client_id: str | None,
    db: AsyncSession,
):
    """Handle refresh_token grant type."""
    if not refresh_token or not client_id:
        return JSONResponse(
            status_code=400,
            content={"error": "invalid_request", "error_description": "Missing required parameters"},
        )

    # Look up refresh token
    token_hash = _hash_code(refresh_token)
    result = await db.execute(
        select(OAuthRefreshToken).where(OAuthRefreshToken.token_hash == token_hash)
    )
    token_record = result.scalar_one_or_none()

    if not token_record:
        return JSONResponse(
            status_code=400,
            content={"error": "invalid_grant", "error_description": "Invalid refresh token"},
        )

    # Check expiry
    if token_record.expires_at < datetime.now(UTC):
        await db.execute(
            delete(OAuthRefreshToken).where(OAuthRefreshToken.id == token_record.id)
        )
        await db.commit()
        return JSONResponse(
            status_code=400,
            content={"error": "invalid_grant", "error_description": "Refresh token expired"},
        )

    # Verify client_id
    if token_record.client_id != client_id:
        return JSONResponse(
            status_code=400,
            content={"error": "invalid_grant", "error_description": "Client ID mismatch"},
        )

    # Get client name
    result = await db.execute(
        select(OAuthClient).where(OAuthClient.client_id == client_id)
    )
    oauth_client = result.scalar_one_or_none()
    client_name = oauth_client.client_name if oauth_client else "Unknown Client"

    try:
        node_url, admin_token, extra_headers = await _get_node_client_context(
            db=db,
            user_id=token_record.user_id,
            username=token_record.username,
        )

        async with NodeClient(node_url, admin_token, extra_headers) as client:
            # Revoke old PCP token
            try:
                await client.revoke_token(token_record.pcp_token_id)
            except NodeClientError:
                pass  # Token may already be revoked/expired

            # Request new grant
            grant_resp = await client.request_grant(
                client_id=f"oauth:{client_id}",
                client_name=client_name,
                scopes_requested=token_record.scope.split(),
                reason=f"OAuth token refresh for {client_name}",
                trust_tier="third_party",
            )

            # Auto-approve
            await client.approve_grant(grant_resp["grant_id"])

            # Claim token
            token_resp = await client.claim_grant_token(
                grant_resp["grant_id"],
                grant_resp["claim_secret"],
            )

    except NodeClientError as e:
        return JSONResponse(
            status_code=500,
            content={"error": "server_error", "error_description": str(e)},
        )

    # Generate new refresh token
    new_refresh_token = secrets.token_urlsafe(32)
    new_refresh_token_hash = _hash_code(new_refresh_token)

    # Update refresh token record
    token_record.token_hash = new_refresh_token_hash
    token_record.pcp_token_id = token_resp["token_id"]
    token_record.pcp_grant_id = grant_resp["grant_id"]
    token_record.expires_at = datetime.now(UTC) + timedelta(days=30)
    await db.commit()

    return TokenResponse(
        access_token=token_resp["token"],
        expires_in=3600,
        refresh_token=new_refresh_token,
        scope=token_record.scope,
    )
