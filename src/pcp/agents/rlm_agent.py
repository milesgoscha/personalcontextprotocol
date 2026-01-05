"""
RLM-style Reference Agent.

This agent demonstrates the Recursive Language Model pattern for
interacting with personal context. Instead of stuffing context into
prompts, it programmatically queries the PCP node and drills down
based on salience.

Key patterns:
1. Query at summary level first
2. Identify high-salience items
3. Drill down to detail level for relevant items
4. Synthesize a reflection
5. Optionally save the reflection back to PCP
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable

from pcp.models.envelope import DisclosureLevel, ObjectType


@dataclass
class PCPClient:
    """
    Client for interacting with a PCP node.

    Makes real HTTP calls to the PCP server. Falls back to mock
    responses if the server is unavailable (for testing).
    """

    base_url: str = "http://localhost:6001"
    token: str | None = None
    subject: str = "rlm-agent"
    _http_client: Any = None
    _mock_mode: bool = False
    _authenticated: bool = False

    # Hooks for testing/mocking
    _query_handler: Callable[..., Any] | None = None
    _reflect_handler: Callable[..., Any] | None = None

    async def _get_client(self) -> Any:
        """Get or create HTTP client."""
        if self._http_client is None:
            import httpx
            self._http_client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=60.0,  # Allow time for Claude API calls
            )
        return self._http_client

    def _headers(self) -> dict[str, str]:
        """Get auth headers."""
        if self.token:
            return {"Authorization": f"Bearer {self.token}"}
        return {}

    async def _check_connection(self) -> bool:
        """Check if server is available."""
        try:
            client = await self._get_client()
            resp = await client.get("/health")
            return resp.status_code == 200
        except Exception:
            return False

    async def authenticate(self) -> bool:
        """Acquire a token from the server."""
        if self._authenticated and self.token:
            return True

        try:
            client = await self._get_client()
            resp = await client.post("/api/token", json={
                "subject": self.subject,
                "scopes": [
                    "query:identity",
                    "query:event.*",
                    "query:learning.*",
                    "query:reflection.*",
                    "observe:event",
                    "learn:write",
                    "reflect:write",
                ],
                "hours": 1,
            })
            resp.raise_for_status()
            token_data = resp.json()
            self.token = token_data["token"]
            self._authenticated = True
            return True
        except Exception as e:
            print(f"[PCPClient] Failed to authenticate: {e}")
            return False

    async def describe(self) -> dict[str, Any]:
        """Get node capabilities."""
        try:
            client = await self._get_client()
            resp = await client.get("/api/describe", headers=self._headers())
            resp.raise_for_status()
            return resp.json()
        except Exception:
            # Fallback mock
            return {
                "node_id": "pcp://local",
                "schema_versions": {
                    "pcp.identity": "1.0",
                    "pcp.event": "1.0",
                    "pcp.learning": "1.0",
                    "pcp.reflection": "0.9",
                },
                "limits": {"max_query_items": 500},
            }

    async def query(
        self,
        object_types: list[str],
        disclosure: str = "summary",
        filter: dict[str, Any] | None = None,
        timerange: dict[str, Any] | None = None,
        limit: int = 100,
        ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Query the PCP node."""
        # Allow handler override for testing
        if self._query_handler:
            return await self._query_handler(
                object_types=object_types,
                disclosure=disclosure,
                filter=filter,
                timerange=timerange,
                limit=limit,
                ids=ids,
            )

        # Ensure authenticated
        if not self._authenticated:
            await self.authenticate()

        try:
            client = await self._get_client()
            payload = {
                "object_types": object_types,
                "disclosure": disclosure,
                "limit": limit,
            }
            if filter:
                payload["filter"] = filter
            if timerange:
                payload["timerange"] = timerange
            if ids:
                payload["ids"] = ids

            resp = await client.post("/api/query", json=payload, headers=self._headers())
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            # Fallback mock with warning
            if not self._mock_mode:
                self._mock_mode = True
                print(f"[PCPClient] Server unavailable, using mock mode: {e}")
            return {"items": [], "count": 0}

    async def reflect(
        self,
        prompt: str,
        scope: str = "custom",
        horizon: dict[str, str] | None = None,
        context: list[str] | None = None,
        save: bool = False,
    ) -> dict[str, Any]:
        """Generate a reflection."""
        # Allow handler override for testing
        if self._reflect_handler:
            return await self._reflect_handler(
                prompt=prompt,
                scope=scope,
                horizon=horizon,
                context=context,
                save=save,
            )

        # Ensure authenticated
        if not self._authenticated:
            await self.authenticate()

        try:
            client = await self._get_client()
            payload = {
                "prompt": prompt,
                "scope": scope,
                "save": save,
            }
            if horizon:
                payload["horizon"] = horizon
            if context:
                payload["context"] = context

            resp = await client.post("/api/reflect", json=payload, headers=self._headers())
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            # Fallback mock with warning
            if not self._mock_mode:
                self._mock_mode = True
                print(f"[PCPClient] Server unavailable, using mock mode: {e}")
            return {
                "content": f"[Mock] Reflection on: {prompt}",
                "sources": [],
                "id": None,
            }

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None


@dataclass
class RLMAgent:
    """
    Reference RLM-style agent that demonstrates the query → drill → reflect pattern.

    This agent shows how to interact with PCP in an RLM-native way:
    - Context is external (not stuffed in prompts)
    - Queries happen in reasoning loops
    - Progressive disclosure reduces token usage
    """

    client: PCPClient
    salience_threshold: float = 0.5
    max_drill_items: int = 10
    trace: list[dict[str, Any]] = field(default_factory=list)

    async def run(
        self,
        prompt: str,
        timerange: dict[str, str] | None = None,
        save_reflection: bool = True,
    ) -> dict[str, Any]:
        """
        Execute the RLM loop:
        1. Query events at summary level
        2. Identify high-salience items
        3. Drill down on relevant items
        4. Generate reflection
        5. Optionally save
        """
        self.trace = []

        # Default timerange to today
        if timerange is None:
            today = datetime.utcnow().date().isoformat()
            timerange = {"after": today}

        # Step 1: Query at summary level
        self._log("query_summary", {"timerange": timerange})
        events_summary = await self.client.query(
            object_types=["event"],
            disclosure="summary",
            timerange=timerange,
            limit=100,
        )
        self._log("query_summary_result", {"count": events_summary.get("count", 0)})

        # Also get learnings for context
        learnings_summary = await self.client.query(
            object_types=["learning"],
            disclosure="summary",
            limit=50,
        )
        self._log("learnings_fetched", {"count": learnings_summary.get("count", 0)})

        # Step 2: Identify high-salience items
        high_salience_ids = self._identify_salient_items(
            events_summary.get("items", [])
        )
        self._log("salience_filter", {"high_salience_count": len(high_salience_ids)})

        # Step 3: Drill down on high-salience items
        detailed_events = []
        if high_salience_ids:
            drill_ids = high_salience_ids[: self.max_drill_items]
            self._log("drill_down", {"ids": drill_ids})

            detail_response = await self.client.query(
                object_types=["event"],
                disclosure="detail",
                ids=drill_ids,
            )
            detailed_events = detail_response.get("items", [])
            self._log("drill_down_result", {"count": len(detailed_events)})

        # Step 4: Build context for reflection
        context_summary = self._build_context_summary(
            events_summary=events_summary.get("items", []),
            detailed_events=detailed_events,
            learnings=learnings_summary.get("items", []),
        )
        self._log("context_built", {"summary_length": len(context_summary)})

        # Step 5: Generate reflection
        self._log("reflect_start", {"prompt": prompt})
        reflection = await self.client.reflect(
            prompt=f"{prompt}\n\nContext:\n{context_summary}",
            scope="custom",
            horizon={
                "start": timerange.get("after", ""),
                "end": timerange.get("before", datetime.utcnow().date().isoformat()),
            },
            context=["events", "learnings"],
            save=save_reflection,
        )
        self._log("reflect_complete", {"saved": save_reflection})

        return {
            "reflection": reflection,
            "trace": self.trace,
            "stats": {
                "events_scanned": events_summary.get("count", 0),
                "events_drilled": len(detailed_events),
                "learnings_used": learnings_summary.get("count", 0),
            },
        }

    def _identify_salient_items(self, items: list[dict[str, Any]]) -> list[str]:
        """
        Identify high-salience items that warrant drilling down.

        In a real implementation, this would use:
        - Embedding similarity to the prompt
        - Recency weighting
        - Explicit salience scores from the items
        - LLM-based relevance scoring
        """
        salient_ids = []

        for item in items:
            # Check if item has salience metadata
            envelope = item.get("envelope", {})
            lineage = envelope.get("lineage", {})
            confidence = lineage.get("confidence", 0.5)

            # Check detail_available flag
            if not item.get("detail_available", True):
                continue

            # Simple heuristic: use confidence as proxy for salience
            if confidence >= self.salience_threshold:
                salient_ids.append(envelope.get("id", ""))

        return [id for id in salient_ids if id]

    def _build_context_summary(
        self,
        events_summary: list[dict[str, Any]],
        detailed_events: list[dict[str, Any]],
        learnings: list[dict[str, Any]],
    ) -> str:
        """Build a context summary for the reflection prompt."""
        lines = []

        # Add learnings first (stable context)
        if learnings:
            lines.append("## Known Facts")
            for learning in learnings[:10]:
                payload = learning.get("payload", {})
                lines.append(f"- {payload.get('summary', 'Unknown')}")
            lines.append("")

        # Add detailed events (drilled down)
        if detailed_events:
            lines.append("## Key Events (Detailed)")
            for event in detailed_events:
                payload = event.get("payload", {})
                detail = payload.get("detail", {})
                lines.append(f"- {payload.get('summary', 'Unknown')}")
                if detail.get("application"):
                    lines.append(f"  App: {detail['application']}")
                if detail.get("url"):
                    lines.append(f"  URL: {detail['url']}")
            lines.append("")

        # Add summary-level events
        if events_summary:
            lines.append("## Activity Summary")
            # Group by hour or category
            summaries = [
                e.get("payload", {}).get("summary", "")
                for e in events_summary[:20]
            ]
            for summary in summaries:
                if summary:
                    lines.append(f"- {summary}")

        return "\n".join(lines)

    def _log(self, step: str, data: dict[str, Any]) -> None:
        """Log a step in the trace."""
        self.trace.append({
            "step": step,
            "timestamp": datetime.utcnow().isoformat(),
            "data": data,
        })


async def main():
    """Demo the RLM agent."""
    client = PCPClient()
    agent = RLMAgent(client=client)

    result = await agent.run(
        prompt="What did I work on today?",
        save_reflection=False,
    )

    print("=== Reflection ===")
    print(result["reflection"]["content"])
    print("\n=== Stats ===")
    print(result["stats"])
    print("\n=== Trace ===")
    for step in result["trace"]:
        print(f"  {step['step']}: {step['data']}")


if __name__ == "__main__":
    asyncio.run(main())
