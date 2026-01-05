#!/usr/bin/env python3
"""
PCP Basic Flow Example

Demonstrates end-to-end usage:
1. Start with identity setup
2. Collector emits events
3. Agent queries and reflects

Prerequisites:
    pip install -e .
    pcp server start  # In another terminal

Usage:
    python examples/basic_flow.py
"""

import asyncio
import httpx
from datetime import datetime, timezone


# Configuration
PCP_BASE_URL = "http://localhost:6001"


async def main():
    """Run the basic PCP flow."""
    async with httpx.AsyncClient(base_url=PCP_BASE_URL, timeout=60.0) as client:
        print("=" * 60)
        print("PCP Basic Flow Example")
        print("=" * 60)

        # Step 1: Check node is running
        print("\n1. Checking node status...")
        try:
            resp = await client.get("/health")
            resp.raise_for_status()
            print(f"   Node healthy: {resp.json()}")
        except httpx.ConnectError:
            print("   ERROR: Node not running. Start with: pcp server start")
            return

        # Step 2: Get capabilities (no auth required)
        print("\n2. Fetching node capabilities...")
        resp = await client.get("/api/describe")
        caps = resp.json()
        print(f"   Node ID: {caps['node_id']}")
        print(f"   Schemas: {list(caps['schema_versions'].keys())}")

        # Step 3: Create a token (via server API so it's in server's token store)
        print("\n3. Creating access token...")
        resp = await client.post("/api/token", json={
            "subject": "example-agent",
            "scopes": [
                "query:identity",
                "identity:write",
                "query:event.*",
                "query:learning.*",
                "observe:event",
                "learn:write",
                "reflect:write",
            ],
            "hours": 1,
        })
        token_data = resp.json()
        token_string = token_data["token"]
        print(f"   Token created for: {token_data['subject']}")
        print(f"   Expires: {token_data['expires_at']}")

        headers = {"Authorization": f"Bearer {token_string}"}

        # Step 4: Set identity
        print("\n4. Setting identity...")
        identity = {
            "name": "Demo User",
            "timezone": "America/Los_Angeles",
            "locale": "en-US",
            "summary": "Demo user for PCP example",
        }
        resp = await client.put("/api/identity", json=identity, headers=headers)
        print(f"   Identity set: {resp.json().get('name')}")

        # Step 5: Emit some events (simulating collector)
        print("\n5. Emitting sample events...")
        events = [
            {
                "envelope": {
                    "type": "event",
                    "tags": ["work", "coding"],
                },
                "payload": {
                    "event_kind": "application.switch",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "summary": "Switched to VS Code to work on PCP",
                    "detail": {
                        "from_application": "Arc",
                        "to_application": "VS Code",
                        "window_title": "pcp/src/pcp/server/app.py",
                    },
                },
            },
            {
                "envelope": {
                    "type": "event",
                    "tags": ["work", "research"],
                },
                "payload": {
                    "event_kind": "application.navigation",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "summary": "Reading RLM paper on arxiv",
                    "detail": {
                        "application": "Arc",
                        "url": "https://arxiv.org/abs/2512.24601",
                        "window_title": "Recursive Language Models",
                    },
                },
            },
            {
                "envelope": {
                    "type": "event",
                    "tags": ["work", "design"],
                },
                "payload": {
                    "event_kind": "application.navigation",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "summary": "Designing PCP protocol spec",
                    "detail": {
                        "application": "Obsidian",
                        "window_title": "PCP Spec Draft",
                    },
                },
            },
        ]

        resp = await client.post(
            "/api/observe",
            json={"objects": events},
            headers=headers,
        )
        result = resp.json()
        print(f"   Stored {result['count']} events")
        for event_id in result["ids"]:
            print(f"     - {event_id}")

        # Step 6: Store a learning
        print("\n6. Recording a learning...")
        resp = await client.post(
            "/api/learn",
            json={
                "key": "preferred_editor",
                "statement": "Prefers VS Code for Python development",
                "confidence": 0.9,
                "category": "preferences",
            },
            headers=headers,
        )
        learn_result = resp.json()
        print(f"   Learning stored: {learn_result['key']}")

        # Step 7: Query events at summary level
        print("\n7. Querying events (summary level)...")
        resp = await client.post(
            "/api/query",
            json={
                "object_types": ["event"],
                "disclosure": "summary",
                "limit": 10,
            },
            headers=headers,
        )
        query_result = resp.json()
        print(f"   Found {query_result['count']} events")
        for item in query_result["items"][:3]:
            summary = item.get("payload", {}).get("summary", "")
            print(f"     - {summary}")

        # Step 8: Query with drill-down to detail
        print("\n8. Drilling down to detail level...")
        if query_result["items"]:
            first_id = query_result["items"][0]["envelope"]["id"]
            resp = await client.post(
                "/api/query",
                json={
                    "object_types": ["event"],
                    "disclosure": "detail",
                    "ids": [first_id],
                },
                headers=headers,
            )
            detail_result = resp.json()
            if detail_result["items"]:
                detail = detail_result["items"][0].get("payload", {}).get("detail", {})
                print(f"   Detail for first event:")
                for k, v in detail.items():
                    print(f"     {k}: {v}")

        # Step 9: Generate a reflection
        print("\n9. Generating reflection...")
        resp = await client.post(
            "/api/reflect",
            json={
                "prompt": "What did I work on in this session?",
                "scope": "custom",
                "context": ["events", "learnings"],
                "save": True,
            },
            headers=headers,
        )
        reflect_result = resp.json()
        print(f"   Reflection: {reflect_result['content'][:100]}...")
        if reflect_result.get("id"):
            print(f"   Saved as: {reflect_result['id']}")

        # Step 10: Query learnings
        print("\n10. Querying learnings...")
        resp = await client.post(
            "/api/query",
            json={
                "object_types": ["learning"],
                "disclosure": "detail",
            },
            headers=headers,
        )
        learnings = resp.json()
        print(f"   Found {learnings['count']} learnings")
        for item in learnings["items"]:
            key = item.get("payload", {}).get("key", "")
            statement = item.get("payload", {}).get("statement", "")
            print(f"     - {key}: {statement}")

        print("\n" + "=" * 60)
        print("Basic flow complete!")
        print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
