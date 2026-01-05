"""
PCP MCP HTTP Server - expose PCP operations via Streamable HTTP transport.

This module provides an MCP endpoint that can be mounted in the PCP FastAPI server,
allowing MCP-compatible agents to connect directly to a hosted PCP node.

The endpoint authenticates using the Authorization header, so agents
configure their MCP client with the node URL and bearer token.

Note: Uses "Streamable HTTP" transport (the newer MCP standard), not legacy SSE.
"""

import json
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from pcp.auth.tokens import Token, verify_token

# Context variable to store the current request's token
_current_token: ContextVar[Token | None] = ContextVar("current_token", default=None)


class TokenAuthMiddleware(BaseHTTPMiddleware):
    """Middleware to extract and validate bearer token from Authorization header."""

    def __init__(self, app, ops):
        super().__init__(app)
        self.ops = ops

    async def dispatch(self, request: Request, call_next):
        # Extract token from Authorization header
        auth_header = request.headers.get("Authorization", "")

        if not auth_header.startswith("Bearer "):
            return Response(
                content="Missing or invalid Authorization header",
                status_code=401,
                media_type="text/plain",
            )

        token_string = auth_header[7:]
        token = verify_token(token_string)

        if token is None:
            return Response(
                content="Invalid or expired token",
                status_code=401,
                media_type="text/plain",
            )

        # Store token in context var for tools to access
        token_var = _current_token.set(token)
        try:
            response = await call_next(request)
            return response
        finally:
            _current_token.reset(token_var)


def get_current_token() -> Token:
    """Get the current request's token from context."""
    token = _current_token.get()
    if token is None:
        raise RuntimeError("No token in context - are you in an MCP request?")
    return token


def create_mcp_sse_app(ops) -> tuple[Starlette, any]:
    """
    Create an MCP HTTP application with PCP tools.

    Args:
        ops: PCPOperations instance for executing operations

    Returns:
        Tuple of (Starlette app, lifespan context manager).
        The lifespan must be composed with the parent app's lifespan
        for proper MCP session manager initialization.
    """
    # Create FastMCP server
    # Use host="0.0.0.0" to disable auto-enabled DNS rebinding protection
    # (which only allows localhost/127.0.0.1). We're behind Traefik with our
    # own auth middleware, so we handle security at that layer.
    mcp = FastMCP(
        "pcp",
        host="0.0.0.0",
        instructions="""PCP (Personal Context Protocol) provides access to the user's personal context.

Available tools:
- pcp_describe: Get node capabilities and info
- pcp_query: Query events, learnings, reflections, or identity
- pcp_observe: Record an event
- pcp_learn: Store a durable fact about the user
- pcp_reflect: Generate a synthesis/reflection

Always start by checking what context is available with pcp_query before making assumptions.""",
    )

    @mcp.tool()
    def pcp_describe() -> str:
        """Get PCP node capabilities and schema versions."""
        token = get_current_token()
        result = ops.describe(token)
        return json.dumps(result, indent=2, default=str)

    @mcp.tool()
    def pcp_query(
        type: Annotated[str, "Type of objects to query: identity, events, learnings, or reflections"],
        disclosure: Annotated[str, "Disclosure level: summary, detail, or raw"] = "summary",
        limit: Annotated[int, "Maximum number of results"] = 100,
        after: Annotated[str | None, "Return events after this datetime (ISO format)"] = None,
        before: Annotated[str | None, "Return events before this datetime (ISO format)"] = None,
        summarize: Annotated[bool, "Return LLM-generated summary instead of raw items"] = False,
    ) -> str:
        """Query personal context (events, learnings, reflections, identity)."""
        token = get_current_token()

        # Map type names to object_types
        type_map = {
            "identity": ["identity"],
            "events": ["event"],
            "learnings": ["learning"],
            "reflections": ["reflection"],
        }
        object_types = type_map.get(type, [type])

        # Build timerange if provided
        timerange = None
        if after or before:
            timerange = {}
            if after:
                timerange["after"] = after
            if before:
                timerange["before"] = before

        result = ops.query(
            token=token,
            object_types=object_types,
            disclosure=disclosure,
            timerange=timerange,
            limit=limit,
            summarize=summarize,
        )
        return json.dumps(result, indent=2, default=str)

    @mcp.tool()
    def pcp_observe(
        event_kind: Annotated[str, "Event type (e.g., 'app_switch', 'insight', 'note')"],
        summary: Annotated[str, "Brief description of the event"],
        detail: Annotated[dict[str, Any] | None, "Structured event data"] = None,
        tags: Annotated[list[str] | None, "Classification tags"] = None,
    ) -> str:
        """Record an event observation into the user's personal context."""
        token = get_current_token()

        event = {
            "envelope": {
                "type": "event",
                "tags": tags or [],
            },
            "payload": {
                "event_kind": event_kind,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "summary": summary,
                "detail": detail or {},
            },
        }
        result = ops.observe(token=token, objects=[event])
        return json.dumps(result, indent=2, default=str)

    @mcp.tool()
    def pcp_learn(
        key: Annotated[str, "Unique key for this learning (e.g., 'preferred_ide', 'work_hours')"],
        statement: Annotated[str, "Human-readable statement of the fact"],
        confidence: Annotated[float, "Confidence score from 0.0 to 1.0"] = 1.0,
        category: Annotated[str | None, "Category: preferences, patterns, or facts"] = None,
    ) -> str:
        """Store or update a durable fact about the user."""
        token = get_current_token()

        result = ops.learn(
            token=token,
            key=key,
            statement=statement,
            confidence=confidence,
            category=category,
        )
        return json.dumps(result, indent=2, default=str)

    @mcp.tool()
    def pcp_reflect(
        prompt: Annotated[str, "What to reflect on (e.g., 'What did I work on today?')"],
        scope: Annotated[str, "Reflection scope: daily, weekly, or custom"] = "custom",
        start: Annotated[str | None, "Start of time range to consider (ISO date)"] = None,
        end: Annotated[str | None, "End of time range to consider (ISO date)"] = None,
        save: Annotated[bool, "Save the reflection to PCP for future reference"] = False,
    ) -> str:
        """Generate a synthesis/reflection over personal context using Claude."""
        token = get_current_token()

        horizon = None
        if start or end:
            horizon = {}
            if start:
                horizon["start"] = start
            if end:
                horizon["end"] = end

        result = ops.reflect(
            token=token,
            prompt=prompt,
            scope=scope,
            horizon=horizon,
            save=save,
        )
        return json.dumps(result, indent=2, default=str)

    # Get the HTTP app - IMPORTANT: preserve lifespan for session manager init
    http_app = mcp.streamable_http_app()

    # Get the lifespan context - this MUST be passed to parent app
    mcp_lifespan = http_app.router.lifespan_context

    # Add auth middleware to the app
    app = Starlette(
        routes=http_app.routes,
        middleware=[Middleware(TokenAuthMiddleware, ops=ops)],
    )

    return app, mcp_lifespan
