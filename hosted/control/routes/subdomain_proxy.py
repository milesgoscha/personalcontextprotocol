"""Subdomain proxy for MCP and API access to user nodes.

Handles requests to {username}.pcp.bio/* by:
1. Extracting username from Host header
2. Looking up user ID from database
3. Proxying to shared PCP node with X-User-Id header

This enables external MCP clients to connect to user nodes via subdomains.
"""

import httpx
from fastapi import APIRouter, Request, HTTPException, status
from fastapi.responses import StreamingResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..database import get_db_session
from ..models import User, Node, NodeStatus


router = APIRouter()


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


@router.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"],
)
async def proxy_to_node(request: Request, path: str) -> Response:
    """Proxy any request to the user's PCP node.

    This is a catch-all route that:
    1. Extracts username from subdomain
    2. Looks up user ID
    3. Forwards request to shared node with X-User-Id header
    """
    settings = get_settings()
    host = request.headers.get("host", "")

    # Skip if this is the main domain (not a subdomain)
    if host == settings.pcp_domain or not host.endswith(f".{settings.pcp_domain}"):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Not found",
        )

    # Get user ID from subdomain
    user_id = await _get_user_id_from_subdomain(host)
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Node not found",
        )

    # Build target URL
    target_url = f"{settings.shared_node_url}/{path}"
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
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Failed to reach node: {e}",
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
