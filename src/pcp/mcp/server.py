"""
PCP MCP Server - expose PCP operations as MCP tools.

This allows any MCP-compatible agent (like Claude Code) to interact
with personal context through standard tool calls.

Tools exposed:
- pcp_describe: Get node capabilities
- pcp_query: Query objects with filtering
- pcp_observe: Ingest events
- pcp_learn: Store learnings
- pcp_reflect: Generate reflections

Usage:
    python -m pcp.mcp.server
    # Or via mcp.json config for Claude Code
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP

from pcp.auth.tokens import create_token
from pcp.server.operations import PCPOperations
from pcp.server.storage import Storage


def make_error_response(error: Exception, operation: str) -> str:
    """Create a structured error response for MCP tools."""
    error_type = type(error).__name__
    return json.dumps({
        "error": True,
        "error_type": error_type,
        "message": str(error),
        "operation": operation,
    }, indent=2)


# Initialize PCP backend
DATA_DIR = Path.home() / ".pcp" / "data"
storage = Storage(data_dir=DATA_DIR)
ops = PCPOperations(storage=storage)

# Create a default token for MCP access
_token_string, _token = create_token(
    subject="mcp-agent",
    scopes=[
        "query:identity",
        "identity:write",
        "query:event.*",
        "query:learning.*",
        "query:reflection.*",
        "observe:event",
        "learn:write",
        "reflect:write",
    ],
)

# Create FastMCP server
mcp = FastMCP("pcp")


@mcp.tool()
def pcp_describe() -> str:
    """Get PCP node capabilities and schema versions."""
    try:
        result = ops.describe(_token)
        return json.dumps(result, indent=2, default=str)
    except Exception as e:
        return make_error_response(e, "describe")


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
    try:
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
            token=_token,
            object_types=object_types,
            disclosure=disclosure,
            timerange=timerange,
            limit=limit,
            summarize=summarize,
        )
        return json.dumps(result, indent=2, default=str)
    except Exception as e:
        return make_error_response(e, "query")


@mcp.tool()
def pcp_observe(
    event_kind: Annotated[str, "Event type (e.g., 'app_switch', 'insight', 'note')"],
    summary: Annotated[str, "Brief description of the event"],
    detail: Annotated[dict[str, Any] | None, "Structured event data"] = None,
    tags: Annotated[list[str] | None, "Classification tags"] = None,
) -> str:
    """Record an event observation into the user's personal context."""
    try:
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
        result = ops.observe(token=_token, objects=[event])
        return json.dumps(result, indent=2, default=str)
    except Exception as e:
        return make_error_response(e, "observe")


@mcp.tool()
def pcp_learn(
    key: Annotated[str, "Unique key for this learning (e.g., 'preferred_ide', 'work_hours')"],
    statement: Annotated[str, "Human-readable statement of the fact"],
    confidence: Annotated[float, "Confidence score from 0.0 to 1.0"] = 1.0,
    category: Annotated[str | None, "Category: preferences, patterns, or facts"] = None,
) -> str:
    """Store or update a durable fact about the user."""
    try:
        result = ops.learn(
            token=_token,
            key=key,
            statement=statement,
            confidence=confidence,
            category=category,
        )
        return json.dumps(result, indent=2, default=str)
    except Exception as e:
        return make_error_response(e, "learn")


@mcp.tool()
def pcp_reflect(
    prompt: Annotated[str, "What to reflect on (e.g., 'What did I work on today?')"],
    scope: Annotated[str, "Reflection scope: daily, weekly, or custom"] = "custom",
    start: Annotated[str | None, "Start of time range to consider (ISO date)"] = None,
    end: Annotated[str | None, "End of time range to consider (ISO date)"] = None,
    save: Annotated[bool, "Save the reflection to PCP for future reference"] = False,
) -> str:
    """Generate a synthesis/reflection over personal context using Claude."""
    try:
        horizon = None
        if start or end:
            horizon = {}
            if start:
                horizon["start"] = start
            if end:
                horizon["end"] = end

        result = ops.reflect(
            token=_token,
            prompt=prompt,
            scope=scope,
            horizon=horizon,
            save=save,
        )
        return json.dumps(result, indent=2, default=str)
    except Exception as e:
        return make_error_response(e, "reflect")


if __name__ == "__main__":
    mcp.run()
