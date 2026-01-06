"""Subdomain proxy middleware for MCP and API access to user nodes.

Handles requests to {username}.pcp.bio/* by:
1. Extracting username from Host header
2. Looking up user ID from database
3. Proxying to shared PCP node with X-User-Id header

This enables external MCP clients to connect to user nodes via subdomains.
"""

import httpx
from fastapi import Request
from fastapi.responses import Response, JSONResponse
from sqlalchemy import select
from starlette.middleware.base import BaseHTTPMiddleware

from ..config import get_settings
from ..database import get_db_session
from ..models import User, Node, NodeStatus


async def _get_user_id_from_subdomain(host: str) -> str | None:
    """Extract username from subdomain and look up user ID.

    Args:
        host: The Host header value (e.g., "miles.pcp.bio")

    Returns:
        The user ID if found, None otherwise.
    """
    settings = get_settings()
    domain = settings.pcp_domain

    # Extract subdomain from host
    # e.g., "miles.pcp.bio" -> "miles"
    if not host.endswith(f".{domain}"):
        return None

    subdomain = host[:-len(f".{domain}")]
    if not subdomain or "." in subdomain:
        return None

    # Look up user by username
    async with get_db_session() as db:
        result = await db.execute(
            select(User).where(User.username == subdomain)
        )
        user = result.scalar_one_or_none()

        if not user:
            return None

        # Verify user has an active node
        result = await db.execute(
            select(Node).where(Node.user_id == user.id)
        )
        node = result.scalar_one_or_none()

        if not node or node.status != NodeStatus.RUNNING:
            return None

        return str(user.id)


def _is_subdomain_request(host: str, main_domain: str) -> bool:
    """Check if the request is from a subdomain."""
    # Not a subdomain if it's the exact main domain
    if host == main_domain or host == f"www.{main_domain}":
        return False

    # Check if it's a subdomain of the main domain
    if host.endswith(f".{main_domain}"):
        subdomain = host[:-len(f".{main_domain}")]
        # Make sure it's a single-level subdomain (no dots)
        return subdomain and "." not in subdomain

    return False


class SubdomainProxyMiddleware(BaseHTTPMiddleware):
    """Middleware that proxies subdomain requests to the shared PCP node."""

    async def dispatch(self, request: Request, call_next):
        settings = get_settings()
        host = request.headers.get("host", "").lower()

        # Strip port if present
        if ":" in host:
            host = host.split(":")[0]

        # Check if this is a subdomain request
        if not _is_subdomain_request(host, settings.pcp_domain):
            # Not a subdomain - pass through to normal routes
            return await call_next(request)

        # This is a subdomain request - handle the proxy
        user_id = await _get_user_id_from_subdomain(host)
        if not user_id:
            return JSONResponse(
                status_code=404,
                content={"detail": "Node not found"},
            )

        # Build target URL
        path = request.url.path
        target_url = f"{settings.shared_node_url}{path}"
        if request.url.query:
            target_url += f"?{request.url.query}"

        # Forward headers, adding X-User-Id
        forward_headers = {}
        for key, value in request.headers.items():
            # Skip hop-by-hop headers and host
            if key.lower() not in ("host", "connection", "keep-alive", "transfer-encoding"):
                forward_headers[key] = value

        forward_headers["X-User-Id"] = user_id

        # Get request body if present
        body = await request.body()

        # Make the proxied request
        async with httpx.AsyncClient(timeout=60.0) as client:
            try:
                response = await client.request(
                    method=request.method,
                    url=target_url,
                    headers=forward_headers,
                    content=body if body else None,
                )
            except httpx.RequestError as e:
                return JSONResponse(
                    status_code=502,
                    content={"detail": f"Failed to reach node: {e}"},
                )

        # Return response, preserving status and headers
        response_headers = dict(response.headers)
        # Remove hop-by-hop headers
        for header in ("connection", "keep-alive", "transfer-encoding", "content-encoding"):
            response_headers.pop(header, None)

        return Response(
            content=response.content,
            status_code=response.status_code,
            headers=response_headers,
            media_type=response.headers.get("content-type"),
        )


# Note: This middleware should be added to the app, not registered as a router
# See app.py for usage
