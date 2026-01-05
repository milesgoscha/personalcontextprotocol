"""
PCP Remote MCP Server - connect to a hosted PCP node via HTTP.

This allows any MCP-compatible agent (like Claude Code) to interact
with a remote PCP node through standard tool calls.

Environment variables:
    PCP_NODE_URL: URL of the PCP node (e.g., http://miles.pcp.localhost)
    PCP_TOKEN: API token for authentication

Usage:
    PCP_NODE_URL=http://miles.pcp.localhost PCP_TOKEN=pcp_xxx python -m pcp.mcp.remote
"""

import json
import os
from datetime import datetime, timezone
from typing import Annotated, Any

import httpx
from mcp.server.fastmcp import FastMCP

# Configuration from environment
PCP_NODE_URL = os.environ.get("PCP_NODE_URL", "http://localhost:6001")
PCP_TOKEN = os.environ.get("PCP_TOKEN", "")

if not PCP_TOKEN:
    raise ValueError("PCP_TOKEN environment variable is required")


def _get_headers() -> dict[str, str]:
    """Get authorization headers."""
    return {"Authorization": f"Bearer {PCP_TOKEN}"}


def _api_call(method: str, endpoint: str, **kwargs) -> dict[str, Any]:
    """Make an API call to the PCP node."""
    url = f"{PCP_NODE_URL.rstrip('/')}{endpoint}"
    with httpx.Client(timeout=30.0) as client:
        response = client.request(method, url, headers=_get_headers(), **kwargs)
        response.raise_for_status()
        return response.json()


# Create FastMCP server
mcp = FastMCP("pcp-remote")


@mcp.tool()
def pcp_describe() -> str:
    """Get PCP node capabilities and schema versions."""
    result = _api_call("POST", "/api/describe", json={})
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

    payload = {
        "object_types": object_types,
        "disclosure": disclosure,
        "limit": limit,
        "summarize": summarize,
    }
    if timerange:
        payload["timerange"] = timerange

    result = _api_call("POST", "/api/query", json=payload)
    return json.dumps(result, indent=2, default=str)


@mcp.tool()
def pcp_observe(
    event_kind: Annotated[str, "Event type (e.g., 'app_switch', 'insight', 'note')"],
    summary: Annotated[str, "Brief description of the event"],
    detail: Annotated[dict[str, Any] | None, "Structured event data"] = None,
    tags: Annotated[list[str] | None, "Classification tags"] = None,
) -> str:
    """Record an event observation into the user's personal context."""
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
    result = _api_call("POST", "/api/observe", json={"objects": [event]})
    return json.dumps(result, indent=2, default=str)


@mcp.tool()
def pcp_learn(
    key: Annotated[str, "Unique key for this learning (e.g., 'preferred_ide', 'work_hours')"],
    statement: Annotated[str, "Human-readable statement of the fact"],
    confidence: Annotated[float, "Confidence score from 0.0 to 1.0"] = 1.0,
    category: Annotated[str | None, "Category: preferences, patterns, or facts"] = None,
) -> str:
    """Store or update a durable fact about the user."""
    payload = {
        "key": key,
        "statement": statement,
        "confidence": confidence,
    }
    if category:
        payload["category"] = category

    result = _api_call("POST", "/api/learn", json=payload)
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
    payload = {
        "prompt": prompt,
        "scope": scope,
        "save": save,
    }
    if start or end:
        payload["horizon"] = {}
        if start:
            payload["horizon"]["start"] = start
        if end:
            payload["horizon"]["end"] = end

    result = _api_call("POST", "/api/reflect", json=payload)
    return json.dumps(result, indent=2, default=str)


def main():
    """Entry point for pcp-mcp command."""
    mcp.run()


if __name__ == "__main__":
    main()
