"""
PCP Node FastAPI Application.

Exposes PCP operations over HTTP with proper auth handling.
"""

import os
from collections import defaultdict
from contextvars import ContextVar
from pathlib import Path
from time import time
from typing import Any

from datetime import datetime
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware

from .config import Config

# Context variable for current user ID (set by middleware in multi-tenant mode)
current_user_id: ContextVar[str | None] = ContextVar("current_user_id", default=None)


# Rate limiting
class RateLimiter:
    """Simple in-memory rate limiter."""

    def __init__(self, requests_per_minute: int = 60):
        self.rpm = requests_per_minute
        self.requests: dict[str, list[float]] = defaultdict(list)

    def is_allowed(self, key: str, limit: int | None = None) -> bool:
        """Check if request is allowed, returns False if rate limited."""
        now = time()
        window_start = now - 60
        max_requests = limit or self.rpm

        # Clean old requests
        self.requests[key] = [t for t in self.requests[key] if t > window_start]

        if len(self.requests[key]) >= max_requests:
            return False

        self.requests[key].append(now)
        return True


_rate_limiter = RateLimiter(requests_per_minute=60)

# Endpoints with stricter limits (prevent abuse of token/grant creation)
STRICT_RATE_LIMIT_PATHS = {
    "/api/token": 10,          # 10 token creations per minute
    "/api/grants/request": 10,  # 10 grant requests per minute
}


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Rate limiting middleware."""

    async def dispatch(self, request: Request, call_next):
        # Determine rate limit key (token subject or IP)
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            # Use first 16 chars of token as key
            key = f"token:{auth_header[7:23]}"
        else:
            # Use client IP
            key = f"ip:{request.client.host if request.client else 'unknown'}"

        # Check for stricter limits on certain paths
        path = request.url.path
        limit = STRICT_RATE_LIMIT_PATHS.get(path)

        if not _rate_limiter.is_allowed(key, limit):
            return StreamingResponse(
                content=iter([b'{"detail": "Rate limit exceeded. Try again later."}']),
                status_code=429,
                media_type="application/json",
            )

        return await call_next(request)


def _extract_user_from_host(host: str, pcp_domain: str | None = None) -> str | None:
    """Extract user_id from subdomain in Host header.

    Example: "alice.pcp.example.com" → "alice"
    Example: "alice.pcp.example.com:443" → "alice"
    """
    if not host:
        return None

    # Remove port if present
    host = host.split(":")[0].lower()

    # Try to extract subdomain
    # For "alice.pcp.example.com" where pcp_domain="pcp.example.com"
    # The user would be "alice"
    if pcp_domain:
        pcp_domain = pcp_domain.lower()
        if host.endswith(f".{pcp_domain}"):
            # Extract everything before .pcp_domain
            subdomain = host[: -(len(pcp_domain) + 1)]
            if subdomain and "." not in subdomain:
                return subdomain

    # Fallback: try to extract first subdomain part
    # For "alice.anything.com", return "alice"
    parts = host.split(".")
    if len(parts) >= 3:  # subdomain.domain.tld
        subdomain = parts[0]
        if subdomain and subdomain.isalnum():
            return subdomain

    return None


class UserContextMiddleware(BaseHTTPMiddleware):
    """
    Middleware for multi-tenant user identification.

    In multi-tenant mode, identifies user from:
    1. X-User-Id header (used by control plane proxy)
    2. Host header subdomain (used by direct agent connections)

    All routes (including /mcp/*) will have access to this context variable.
    """

    async def dispatch(self, request: Request, call_next):
        user_id = None

        if Config.MULTI_TENANT:
            # First, try X-User-Id header (control plane proxy)
            user_id = request.headers.get("X-User-Id")

            # Fallback: extract from Host header subdomain (direct agent access)
            if not user_id:
                host = request.headers.get("Host", "")
                # Get PCP domain from config or environment
                pcp_domain = os.getenv("PCP_DOMAIN")
                user_id = _extract_user_from_host(host, pcp_domain)

            # Allow health checks without user_id
            if not user_id and not request.url.path.startswith("/health"):
                return JSONResponse(
                    {"error": "User identification required. Provide X-User-Id header or use subdomain."},
                    status_code=400,
                )

        # Set user_id in context for this request
        token = current_user_id.set(user_id)
        try:
            response = await call_next(request)
            return response
        finally:
            current_user_id.reset(token)


def get_current_user_id() -> str | None:
    """Get the current user ID from context (for use in dependency injection)."""
    return current_user_id.get()


from pcp.auth.audit import AuditLog
from pcp.auth.grants import Grant, GrantStatus, GrantStore, TrustTier
from pcp.auth.scopes import Operation, validate_scope
from pcp.auth.tokens import Token, TokenStore, init_token_store
from pcp.models.envelope import ObjectType

from .operations import PCPOperations
from .storage import Storage


# Request/Response models

class QueryRequest(BaseModel):
    object_types: list[str]
    disclosure: str = "summary"
    filter: dict[str, Any] | None = None
    timerange: dict[str, Any] | None = None
    limit: int = 100
    cursor: str | None = None
    ids: list[str] | None = None
    summarize: bool = False
    tags_include: list[str] | None = None  # Require ALL of these tags
    tags_exclude: list[str] | None = None  # Exclude items with ANY of these


class ObserveRequest(BaseModel):
    objects: list[dict[str, Any]]
    ingest_mode: str = "append"
    dedupe_keys: list[str] | None = None


class LearnRequest(BaseModel):
    key: str
    statement: str
    confidence: float = 1.0
    category: str | None = None
    derived_from: list[str] | None = None
    upsert: bool = True


class ReflectRequest(BaseModel):
    prompt: str
    scope: str = "custom"
    horizon: dict[str, str] | None = None
    context: list[str] | None = None
    save: bool = False
    replace_scope: bool = False


# Note: get_token and require_token dependencies are defined inside create_app()
# to access user-scoped TokenStore in multi-tenant mode.


def has_admin_access(token: Token) -> bool:
    """
    Check if token has admin access.

    Admin access is granted if:
    - Token has pcp:admin scope, OR
    - Token is from local trust tier (auto-approved local agents)
    """
    has_admin_scope = any(
        str(s) == "pcp:admin" or str(s).startswith("pcp:admin")
        for s in token.scopes
    )
    is_local = token.trust_tier == "local"
    return has_admin_scope or is_local


def require_admin(token: Token) -> None:
    """
    Require admin access for grant management operations.

    Admin access is granted if:
    - Token has pcp:admin scope, OR
    - Token is from local trust tier (auto-approved local agents)

    NOTE: Currently local trust tier implies full admin access. This matches
    the trust model where local agents are on the user's machine and fully
    trusted. If we ever need "local but sandboxed" agents, this should be
    changed to require explicit pcp:admin scope even for local tokens.
    """
    if not has_admin_access(token):
        raise HTTPException(
            status_code=403,
            detail="Admin access required (pcp:admin scope or local trust tier)"
        )


def create_app(
    data_dir: str | Path | None = None,
    public_url: str | None = None,
    node_id: str | None = None,
) -> FastAPI:
    """
    Create the PCP Node FastAPI application.

    Args:
        data_dir: Directory for persistent storage
        public_url: Public URL for this node (for discovery)
        node_id: Node identifier (pcp:// URI)

    In multi-tenant mode (PCP_MULTI_TENANT=true):
        - X-User-Id header is required on all requests
        - Storage is scoped to /data/{user_id}/
        - Traefik is expected to add X-User-Id based on subdomain
    """
    if data_dir is None:
        data_dir = Config.DATA_DIR
    data_dir = Path(data_dir)

    # URLs from args, environment, or defaults
    _public_url = public_url or os.environ.get("PCP_PUBLIC_URL", "http://localhost:6001")
    _node_id = node_id or Config.NODE_ID

    # In single-tenant mode, create storage once at startup
    # In multi-tenant mode, storage is created per-request via dependency injection
    if not Config.MULTI_TENANT:
        storage = Storage(data_dir=data_dir)
        audit_log = AuditLog(persist_path=str(data_dir / "audit.jsonl"))
        ops = PCPOperations(storage=storage, node_id=_node_id, audit_log=audit_log)
        token_store = TokenStore(data_dir=data_dir)
        grant_store = GrantStore(data_dir=data_dir, token_store=token_store)
        # Also initialize global token store for backward compatibility
        init_token_store(data_dir)
    else:
        # These will be overridden by dependency injection per request
        storage = None
        ops = None
        grant_store = None
        token_store = None
        audit_log = None

    # Dependency injection functions for multi-tenant mode
    def get_storage() -> Storage:
        """Get storage instance, scoped to current user in multi-tenant mode."""
        user_id = get_current_user_id()
        if Config.MULTI_TENANT:
            return Storage(data_dir=data_dir, user_id=user_id)
        return storage

    def get_grant_store() -> GrantStore:
        """Get grant store, scoped to current user in multi-tenant mode."""
        user_id = get_current_user_id()
        if Config.MULTI_TENANT:
            # Pass user-scoped token_store so issued tokens use per-user signing keys
            return GrantStore(data_dir=data_dir, user_id=user_id, token_store=get_token_store())
        return grant_store

    def get_token_store() -> TokenStore:
        """Get token store, scoped to current user in multi-tenant mode."""
        user_id = get_current_user_id()
        if Config.MULTI_TENANT:
            return TokenStore(data_dir=data_dir, user_id=user_id)
        return token_store

    def get_audit_log() -> AuditLog:
        """Get audit log, scoped to current user in multi-tenant mode."""
        user_id = get_current_user_id()
        if Config.MULTI_TENANT:
            return AuditLog(data_dir=str(data_dir), user_id=user_id)
        return audit_log

    def get_ops() -> PCPOperations:
        """Get operations instance, scoped to current user in multi-tenant mode."""
        if Config.MULTI_TENANT:
            user_storage = get_storage()
            user_audit_log = get_audit_log()
            return PCPOperations(storage=user_storage, node_id=_node_id, audit_log=user_audit_log)
        return ops

    # Token verification dependencies (defined here to access user-scoped token store)
    async def get_token(
        request: Request,
        authorization: str | None = Header(None),
    ) -> Token | None:
        """Extract and verify token from Authorization header.

        In multi-tenant mode, requests with X-User-Id header from the control plane
        are treated as admin requests (synthetic admin token is returned).
        """
        # In multi-tenant mode, check for control plane admin access
        if Config.MULTI_TENANT:
            x_user_id = request.headers.get("X-User-Id")
            # If we have X-User-Id but no Authorization, this is a control plane request
            # Control plane has already authenticated the user via session cookies
            if x_user_id and not authorization:
                from datetime import datetime, timedelta, UTC
                # Return a synthetic admin token for control plane requests
                # trust_tier is stored in metadata, not as a direct field
                return Token(
                    token_id="control-plane",
                    subject="control-plane",
                    scopes=["pcp:admin"],
                    issued_at=datetime.now(UTC),
                    expires_at=datetime.now(UTC) + timedelta(hours=1),
                    metadata={"trust_tier": "local"},
                )

        if not authorization:
            return None

        if not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Invalid authorization header")

        token_string = authorization[7:]
        ts = get_token_store()
        token = ts.verify(token_string)

        if token is None:
            raise HTTPException(status_code=401, detail="Invalid or expired token")

        return token

    async def require_token(token: Token | None = Depends(get_token)) -> Token:
        """Require a valid token."""
        if token is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        return token

    app = FastAPI(
        title="PCP Node",
        description="Personal Context Protocol Node Server",
        version="0.1.0",
    )

    # Add middleware (order matters - user context must be set before rate limiting uses it)
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(UserContextMiddleware)

    @app.get("/")
    async def root():
        """Root endpoint."""
        return {"name": "PCP Node", "version": "0.1.0"}

    @app.get("/.well-known/pcp")
    async def well_known_pcp():
        """
        PCP discovery endpoint.

        Returns node metadata for pcp://me resolution.
        Agents use this to discover the API endpoint, auth, and grants URLs.
        """
        return {
            "node_id": _node_id,
            "version": "0.1.0",
            "endpoint": f"{_public_url}/api",
            "auth": {
                "type": "bearer",
                "token_endpoint": f"{_public_url}/api/token",
                "grants_endpoint": f"{_public_url}/api/grants",
            },
            "capabilities": {
                "operations": ["describe", "query", "observe", "learn", "reflect"],
                "disclosure_levels": ["summary", "detail", "raw"],
                "trust_tiers": ["local", "first_party_remote", "third_party"],
            },
            "mcp": {
                "available": True,
                "transports": {
                    "sse": {
                        "endpoint": f"{_public_url}/mcp",
                        "auth": "bearer",
                    },
                    "stdio": {
                        "command": "pcp-mcp",
                        "note": "Requires PCP_NODE_URL and PCP_TOKEN env vars",
                    },
                },
            },
        }

    def _get_public_url(request: Request) -> str:
        """Get the public URL, preferring X-Forwarded-Host for proxied requests."""
        forwarded_host = request.headers.get("x-forwarded-host")
        if forwarded_host:
            return f"https://{forwarded_host}"
        return _public_url

    # OAuth discovery endpoints - return 404 for all OAuth endpoints so clients
    # fall back to Bearer token authentication instead of trying OAuth flow.
    # See: https://github.com/anthropics/claude-code/issues/2831

    @app.get("/.well-known/oauth-protected-resource")
    @app.get("/.well-known/oauth-protected-resource/{path:path}")
    @app.get("/.well-known/oauth-authorization-server")
    @app.get("/.well-known/oauth-authorization-server/{path:path}")
    @app.get("/.well-known/openid-configuration")
    @app.get("/.well-known/openid-configuration/{path:path}")
    async def oauth_discovery_not_supported(request: Request, path: str = ""):
        """Return 404 for all OAuth discovery endpoints with proper JSON error."""
        return JSONResponse(
            status_code=404,
            content={
                "error": "not_found",
                "error_description": "OAuth is not supported. Use Bearer token authentication.",
            },
        )

    @app.post("/register")
    async def oauth_register_not_supported():
        """OAuth dynamic client registration - not supported, return proper error."""
        return JSONResponse(
            status_code=400,
            content={
                "error": "invalid_request",
                "error_description": "OAuth dynamic client registration is not supported. Use Bearer token authentication.",
            },
        )

    @app.get("/api/describe")
    async def describe(token: Token | None = Depends(get_token)):
        """Get node capabilities."""
        return get_ops().describe(token)

    @app.post("/api/query")
    async def query(request: QueryRequest, token: Token = Depends(require_token)):
        """Query objects."""
        try:
            return get_ops().query(
                token=token,
                object_types=request.object_types,
                disclosure=request.disclosure,
                filter=request.filter,
                timerange=request.timerange,
                limit=request.limit,
                cursor=request.cursor,
                ids=request.ids,
                summarize=request.summarize,
                tags_include=request.tags_include,
                tags_exclude=request.tags_exclude,
            )
        except ValueError as e:
            raise HTTPException(status_code=403, detail=str(e))

    @app.post("/api/observe")
    async def observe(request: ObserveRequest, token: Token = Depends(require_token)):
        """Ingest events."""
        try:
            return get_ops().observe(
                token=token,
                objects=request.objects,
                ingest_mode=request.ingest_mode,
                dedupe_keys=request.dedupe_keys,
            )
        except ValueError as e:
            raise HTTPException(status_code=403, detail=str(e))

    @app.post("/api/learn")
    async def learn(request: LearnRequest, token: Token = Depends(require_token)):
        """Store a learning."""
        try:
            return get_ops().learn(
                token=token,
                key=request.key,
                statement=request.statement,
                confidence=request.confidence,
                category=request.category,
                derived_from=request.derived_from,
                upsert=request.upsert,
            )
        except ValueError as e:
            raise HTTPException(status_code=403, detail=str(e))

    @app.post("/api/reflect")
    async def reflect(request: ReflectRequest, token: Token = Depends(require_token)):
        """Generate a reflection."""
        try:
            return get_ops().reflect(
                token=token,
                prompt=request.prompt,
                scope=request.scope,
                horizon=request.horizon,
                context=request.context,
                save=request.save,
                replace_scope=request.replace_scope,
            )
        except ValueError as e:
            raise HTTPException(status_code=403, detail=str(e))

    # Identity endpoints

    @app.get("/api/identity")
    async def get_identity(token: Token = Depends(require_token)):
        """Get user identity."""
        try:
            validate_scope(
                token.scopes,
                Operation.IDENTITY,
                ObjectType.IDENTITY,
                requires_write=False,
            )
        except ValueError as e:
            raise HTTPException(status_code=403, detail=str(e))

        identity = get_storage().get_identity()
        if identity is None:
            raise HTTPException(status_code=404, detail="Identity not set")
        return identity

    @app.put("/api/identity")
    async def set_identity(identity: dict[str, Any], token: Token = Depends(require_token)):
        """Set user identity."""
        try:
            validate_scope(
                token.scopes,
                Operation.IDENTITY,
                ObjectType.IDENTITY,
                requires_write=True,
            )
        except ValueError as e:
            raise HTTPException(status_code=403, detail=str(e))

        return get_storage().set_identity(identity)

    # Token endpoint (for local dev - in production, use proper auth flow)

    class TokenRequest(BaseModel):
        subject: str
        scopes: list[str]
        hours: int = 24

    @app.post("/api/token")
    async def create_token_endpoint(request: TokenRequest):
        """Create an access token (dev only)."""
        from datetime import timedelta

        ts = get_token_store()
        token_string, token_obj = ts.create(
            subject=request.subject,
            scopes=request.scopes,
            expires_in=timedelta(hours=request.hours),
        )
        return {
            "token": token_string,
            "token_id": token_obj.token_id,
            "subject": token_obj.subject,
            "expires_at": token_obj.expires_at.isoformat(),
        }

    @app.get("/api/tokens")
    async def list_tokens_endpoint(token: Token = Depends(require_token)):
        """List all tokens. Requires admin access."""
        require_admin(token)
        ts = get_token_store()
        tokens = ts.list_tokens()
        return {
            "tokens": [
                {
                    "token_id": t.token_id,
                    "subject": t.subject,
                    "scopes": [str(s) for s in t.scopes],
                    "issued_at": t.issued_at.isoformat(),
                    "expires_at": t.expires_at.isoformat(),
                    "trust_tier": t.trust_tier,
                }
                for t in tokens
            ],
            "count": len(tokens),
        }

    @app.delete("/api/tokens/{token_id}")
    async def revoke_token_endpoint(token_id: str, token: Token = Depends(require_token)):
        """Revoke a token by ID. Requires admin access."""
        require_admin(token)
        ts = get_token_store()
        success = ts.revoke(token_id)
        if not success:
            raise HTTPException(status_code=404, detail="Token not found")
        return {"status": "revoked", "token_id": token_id}

    # Grant management endpoints

    class GrantRequest(BaseModel):
        client_id: str
        client_name: str
        scopes_requested: list[str]
        reason: str
        callback_url: str | None = None
        trust_tier: str = "third_party"

    class GrantApproval(BaseModel):
        scopes: list[str] | None = None
        lifetime_hours: int | None = None

    class GrantDenial(BaseModel):
        reason: str | None = None

    @app.post("/api/grants/request")
    async def request_grant(request: GrantRequest):
        """
        Request a grant for API access.

        Third-party agents use this to request scoped access.
        Returns a grant_id and claim_secret. The claim_secret must be stored
        securely and provided when claiming the token.
        """
        try:
            tier = TrustTier(request.trust_tier)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid trust tier: {request.trust_tier}")

        grant, claim_secret = get_grant_store().create(
            client_id=request.client_id,
            client_name=request.client_name,
            scopes_requested=request.scopes_requested,
            reason=request.reason,
            callback_url=request.callback_url,
            trust_tier=tier,
        )

        return {
            "grant_id": grant.grant_id,
            "claim_secret": claim_secret,  # Client must store this securely!
            "status": grant.status.value,
            "message": "Grant approved automatically" if grant.status == GrantStatus.APPROVED else "Grant pending approval",
        }

    @app.get("/api/grants")
    async def list_grants(
        status: str | None = None,
        client_id: str | None = None,
        token: Token = Depends(require_token),
    ):
        """List grants. Requires admin access."""
        require_admin(token)
        status_filter = GrantStatus(status) if status else None
        grants = get_grant_store().list_grants(status=status_filter, client_id=client_id)
        return {
            "grants": [g.to_dict() for g in grants],
            "count": len(grants),
        }

    @app.get("/api/grants/{grant_id}")
    async def get_grant(grant_id: str, token: Token = Depends(require_token)):
        """
        Get a specific grant by ID.

        Requires either:
        - Admin access (can see any grant), OR
        - The caller is the original requester (can only see their own grant)
        """
        grant = get_grant_store().get(grant_id)
        if not grant:
            raise HTTPException(status_code=404, detail="Grant not found")

        # Check if caller is admin or the original requester
        is_admin = token.trust_tier == "local" or any(
            str(s) == "pcp:admin" for s in token.scopes
        )
        is_owner = token.subject == grant.client_id

        if not is_admin and not is_owner:
            raise HTTPException(status_code=403, detail="Access denied")

        return grant.to_dict()

    @app.post("/api/grants/{grant_id}/approve")
    async def approve_grant(
        grant_id: str,
        approval: GrantApproval | None = None,
        token: Token = Depends(require_token),
    ):
        """Approve a pending grant. Requires admin access."""
        require_admin(token)
        approval = approval or GrantApproval()
        grant = get_grant_store().approve(
            grant_id=grant_id,
            scopes=approval.scopes,
            lifetime_hours=approval.lifetime_hours,
        )
        if not grant:
            raise HTTPException(status_code=400, detail="Grant not found or not pending")
        return {
            "grant_id": grant.grant_id,
            "status": grant.status.value,
            "scopes_approved": grant.scopes_approved,
            "expires_at": grant.expires_at.isoformat() if grant.expires_at else None,
        }

    @app.post("/api/grants/{grant_id}/deny")
    async def deny_grant(
        grant_id: str,
        denial: GrantDenial | None = None,
        token: Token = Depends(require_token),
    ):
        """Deny a pending grant. Requires admin access."""
        require_admin(token)
        denial = denial or GrantDenial()
        grant = get_grant_store().deny(grant_id=grant_id, reason=denial.reason)
        if not grant:
            raise HTTPException(status_code=400, detail="Grant not found or not pending")
        return {
            "grant_id": grant.grant_id,
            "status": grant.status.value,
            "denial_reason": grant.denial_reason,
        }

    @app.post("/api/grants/{grant_id}/revoke")
    async def revoke_grant(
        grant_id: str,
        token: Token = Depends(require_token),
    ):
        """Revoke an approved grant. Requires admin access."""
        require_admin(token)
        grant = get_grant_store().revoke(grant_id=grant_id)
        if not grant:
            raise HTTPException(status_code=400, detail="Grant not found or not approved")
        return {
            "grant_id": grant.grant_id,
            "status": grant.status.value,
        }

    class TokenClaimRequest(BaseModel):
        claim_secret: str

    @app.post("/api/grants/{grant_id}/token")
    async def issue_grant_token(grant_id: str, request: TokenClaimRequest):
        """
        Issue a token for an approved grant.

        Requires the claim_secret that was returned when the grant was created.
        This ensures only the original requester can claim the token.
        """
        result = get_grant_store().issue_token(grant_id=grant_id, claim_secret=request.claim_secret)
        if not result:
            raise HTTPException(
                status_code=400,
                detail="Grant not approved, expired, or invalid claim secret"
            )

        token_string, grant = result
        return {
            "token": token_string,
            "grant_id": grant.grant_id,
            "scopes": grant.scopes_approved,
            "expires_at": grant.expires_at.isoformat() if grant.expires_at else None,
            "trust_tier": grant.trust_tier.value,
        }

    # Audit endpoint

    @app.get("/api/audit")
    async def list_audit_events(
        operation: str | None = None,
        requester: str | None = None,
        since: str | None = None,
        before: str | None = None,
        limit: int = 100,
        offset: int = 0,
        token: Token = Depends(require_token),
    ):
        """
        List audit events.

        - Admin users can query all events
        - Non-admin users can only query their own events (requester = token.subject)
        """
        is_admin = has_admin_access(token)

        # Non-admins can only query their own history
        if not is_admin:
            if requester and requester != token.subject:
                raise HTTPException(
                    status_code=403,
                    detail="Can only query your own audit history"
                )
            requester = token.subject  # Force filter to own events

        # Use local get_audit_log() which scopes to user in multi-tenant mode
        user_audit_log = get_audit_log()

        # Parse time filters
        since_dt = datetime.fromisoformat(since) if since else None
        before_dt = datetime.fromisoformat(before) if before else None

        # Query with filters
        events = user_audit_log.query(
            operation=operation,
            requester=requester,
            since=since_dt,
            limit=limit + offset,  # Get extra for offset handling
        )

        # Apply before filter
        if before_dt:
            events = [e for e in events if e.timestamp <= before_dt]

        # Apply pagination
        total = len(events)
        paginated = events[offset:offset + limit]

        return {
            "events": [e.to_pcp_event() for e in paginated],
            "count": len(paginated),
            "total": total,
            "has_more": total > offset + limit,
        }

    # Export endpoint

    @app.get("/api/export")
    async def export_objects(
        type: str | None = None,
        token: Token = Depends(require_token),
    ):
        """
        Export all objects as streaming JSONL.

        Requires admin access. Returns all objects (or filtered by type)
        as newline-delimited JSON for data portability.
        """
        import json

        require_admin(token)
        user_storage = get_storage()

        async def generate():
            for obj_id, obj in user_storage._objects.items():
                # Filter by type if specified
                if type:
                    obj_type = obj.get("envelope", {}).get("type")
                    if obj_type != type:
                        continue
                yield json.dumps(obj) + "\n"

        return StreamingResponse(
            generate(),
            media_type="application/x-ndjson",
            headers={"Content-Disposition": "attachment; filename=pcp-export.jsonl"}
        )

    # Import endpoint

    @app.post("/api/import")
    async def import_objects(
        request: Request,
        merge: bool = True,
        token: Token = Depends(require_token),
    ):
        """
        Import objects from JSONL data.

        Requires admin access. Accepts newline-delimited JSON objects.
        Each object must have an envelope with at least 'type' field.

        Query params:
            merge: If true (default), existing objects are updated.
                   If false, existing objects are skipped.
        """
        import json

        require_admin(token)
        user_storage = get_storage()

        body = await request.body()
        lines = body.decode("utf-8").strip().split("\n")

        imported = 0
        skipped = 0
        errors = []

        for i, line in enumerate(lines):
            if not line.strip():
                continue

            try:
                obj = json.loads(line)

                # Validate structure
                envelope = obj.get("envelope")
                if not envelope:
                    errors.append({"line": i + 1, "error": "Missing envelope"})
                    continue

                if not envelope.get("type"):
                    errors.append({"line": i + 1, "error": "Missing envelope.type"})
                    continue

                # Check if object already exists
                obj_id = envelope.get("id")
                if obj_id and obj_id in user_storage._objects:
                    if merge:
                        user_storage.store(obj)
                        imported += 1
                    else:
                        skipped += 1
                else:
                    user_storage.store(obj)
                    imported += 1

            except json.JSONDecodeError as e:
                errors.append({"line": i + 1, "error": f"Invalid JSON: {e}"})

        return {
            "imported": imported,
            "skipped": skipped,
            "errors": errors[:10],  # Limit error details
            "total_errors": len(errors),
        }

    # Health check

    @app.get("/health")
    async def health():
        """Health check endpoint."""
        return {"status": "healthy"}

    # Mount MCP HTTP endpoint
    from contextlib import asynccontextmanager
    from pcp.mcp.sse import create_mcp_sse_app

    # In multi-tenant mode, MCP uses get_ops() and get_token_store() for user-scoped resources
    # In single-tenant mode, we use the pre-created instances
    mcp_app, mcp_lifespan = create_mcp_sse_app(
        ops if not Config.MULTI_TENANT else None,
        get_ops_fn=get_ops,
        get_token_store_fn=get_token_store if Config.MULTI_TENANT else None,
    )

    # Create a composed lifespan that initializes MCP's session manager
    @asynccontextmanager
    async def lifespan(app):
        # Run MCP's lifespan context to initialize session manager
        async with mcp_lifespan(app):
            yield

    # Update the app's lifespan
    app.router.lifespan_context = lifespan

    # Mount the MCP app
    app.mount("/mcp", mcp_app)

    return app


# Default app instance for uvicorn
app = create_app()
