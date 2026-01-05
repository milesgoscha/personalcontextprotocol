"""
PCP Operations - describe, query, observe, learn, reflect.

These implement the core PCP protocol operations with auth and audit baked in.
"""

import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pcp.auth.audit import log_operation
from pcp.auth.grants import TrustTier
from pcp.auth.redactions import apply_redactions, get_effective_disclosure
from pcp.auth.scopes import Operation, ScopeSet, validate_scope
from pcp.auth.tokens import Token
from pcp.models.envelope import DisclosureLevel, ObjectType

from .storage import Storage


def get_anthropic_client():
    """Get Anthropic client if API key is available."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    try:
        import anthropic
        return anthropic.Anthropic(api_key=api_key)
    except ImportError:
        return None


@dataclass
class PCPOperations:
    """
    PCP protocol operations.

    All operations:
    1. Validate token scopes
    2. Execute the operation
    3. Log to audit trail
    4. Return results
    """

    storage: Storage
    node_id: str = "pcp://local"

    def describe(self, token: Token | None = None) -> dict[str, Any]:
        """
        Return node capabilities.

        Always allowed, but logged for audit purposes.
        """
        requester = token.subject if token else "anonymous"

        log_operation(
            operation="describe",
            requester=requester,
            token_id=token.token_id if token else None,
            success=True,
        )

        return {
            "node_id": self.node_id,
            "schema_versions": {
                "pcp.identity": "1.0",
                "pcp.event": "1.0",
                "pcp.learning": "1.0",
                "pcp.reflection": "0.9",
            },
            "transports": [
                {"type": "https", "endpoint": "http://localhost:6001/api"},
                {"type": "mcp", "endpoint": "mcp://pcp-local"},
            ],
            "auth": {
                "supported": ["bearer"],
                "scopes": [
                    "query:identity",
                    "query:event.summary",
                    "query:event.detail",
                    "query:event.*",
                    "query:learning.*",
                    "query:reflection.*",
                    "observe:event",
                    "learn:write",
                    "reflect:write",
                ],
            },
            "limits": {
                "max_query_items": 500,
                "max_attachment": 10485760,
            },
        }

    def query(
        self,
        token: Token,
        object_types: list[str],
        disclosure: str = "summary",
        filter: dict[str, Any] | None = None,
        timerange: dict[str, Any] | None = None,
        limit: int = 100,
        cursor: str | None = None,
        ids: list[str] | None = None,
        summarize: bool = False,
        tags_include: list[str] | None = None,
        tags_exclude: list[str] | None = None,
    ) -> dict[str, Any]:
        """
        Query objects with filtering and progressive disclosure.
        """
        # Parse types and disclosure
        types = [ObjectType(t) for t in object_types]
        disc_level = DisclosureLevel(disclosure)

        # Validate scopes for each requested type
        for obj_type in types:
            validate_scope(token.scopes, Operation.QUERY, obj_type, disc_level)

        # Parse time range
        time_from = None
        time_to = None
        if timerange:
            if "from" in timerange:
                time_from = datetime.fromisoformat(timerange["from"])
            elif "after" in timerange:
                # Support relative times like "today"
                if timerange["after"] == "today":
                    time_from = datetime.utcnow().replace(hour=0, minute=0, second=0)
                else:
                    time_from = datetime.fromisoformat(timerange["after"])

            if "to" in timerange:
                time_to = datetime.fromisoformat(timerange["to"])
            elif "before" in timerange:
                time_to = datetime.fromisoformat(timerange["before"])

        # Parse filter
        tags = filter.get("tags") if filter else None
        predicates = filter.get("predicates") if filter else None

        # Execute query
        result = self.storage.query(
            object_types=types,
            tags=tags,
            tags_include=tags_include,
            tags_exclude=tags_exclude,
            time_from=time_from,
            time_to=time_to,
            predicates=predicates,
            disclosure=disc_level,
            limit=limit,
            cursor=cursor,
            ids=ids,
        )

        # Determine trust tier from token metadata
        try:
            trust_tier = TrustTier(token.trust_tier)
        except ValueError:
            trust_tier = TrustTier.LOCAL  # Default to local for dev tokens

        # Get effective disclosure after applying tier ceiling
        effective_disclosure = get_effective_disclosure(trust_tier, disclosure)

        # Log operation
        log_operation(
            operation="query",
            requester=token.subject,
            token_id=token.token_id,
            object_types=types,
            disclosure_level=disc_level,
            request_filter=filter,
            success=True,
            result_count=result.count,
        )

        # Apply redactions and add disclosure flags to each item
        items_with_flags = []
        total_redacted = 0
        for item in result.items:
            item_copy = dict(item) if isinstance(item, dict) else item

            # Apply trust-tier redactions
            redacted_item, redacted_fields = apply_redactions(
                item_copy, trust_tier, disclosure
            )
            if redacted_fields:
                total_redacted += 1

            # Check if detail/raw are available based on what's stored (before redaction)
            payload = item_copy.get("payload", {})
            redacted_item["detail_available"] = (
                "detail" in payload
                and trust_tier in (TrustTier.LOCAL, TrustTier.FIRST_PARTY_REMOTE)
            )
            redacted_item["raw_available"] = (
                "raw_ref" in payload
                and trust_tier == TrustTier.LOCAL
            )
            redacted_item["disclosure_level"] = effective_disclosure
            items_with_flags.append(redacted_item)

        # Build response
        response = {
            "items": items_with_flags,
            "count": result.count,
            "effective_disclosure": effective_disclosure,
        }

        # Add redaction stats if any items were redacted
        if total_redacted > 0:
            response["redaction_applied"] = {
                "trust_tier": trust_tier.value,
                "items_redacted": total_redacted,
            }

        if result.next_cursor:
            response["next_page"] = {
                "cursor": result.next_cursor,
                "remaining_estimate": result.remaining_estimate,
            }

        # If summarize requested, generate LLM summary
        if summarize:
            response["summary"] = self._generate_summary(result.items)
            response["sources"] = [
                item.get("envelope", {}).get("id")
                for item in result.items
            ]

        return response

    def observe(
        self,
        token: Token,
        objects: list[dict[str, Any]],
        ingest_mode: str = "append",
        dedupe_keys: list[str] | None = None,
    ) -> dict[str, Any]:
        """
        Ingest events from collectors.
        """
        # Validate scope
        validate_scope(token.scopes, Operation.OBSERVE, ObjectType.EVENT)

        stored_ids = []
        for obj in objects:
            # Ensure it's an event
            envelope = obj.get("envelope", {})
            envelope["type"] = "event"
            obj["envelope"] = envelope

            # Add source lineage
            if "lineage" not in envelope:
                envelope["lineage"] = {}
            envelope["lineage"]["sources"] = envelope["lineage"].get("sources", [])
            if token.subject not in envelope["lineage"]["sources"]:
                envelope["lineage"]["sources"].append(f"collector:{token.subject}")

            # Store
            obj_id = self.storage.store(obj)
            stored_ids.append(obj_id)

        # Log operation
        log_operation(
            operation="observe",
            requester=token.subject,
            token_id=token.token_id,
            object_types=[ObjectType.EVENT],
            success=True,
            result_count=len(stored_ids),
        )

        return {
            "ids": stored_ids,
            "count": len(stored_ids),
        }

    def learn(
        self,
        token: Token,
        key: str,
        statement: str,
        confidence: float = 1.0,
        category: str | None = None,
        derived_from: list[str] | None = None,
        upsert: bool = True,
    ) -> dict[str, Any]:
        """
        Store or update a learning.
        """
        # Validate scope
        validate_scope(token.scopes, Operation.LEARN, ObjectType.LEARNING)

        # Check for existing learning with same key
        existing = None
        if upsert:
            result = self.storage.query(
                object_types=[ObjectType.LEARNING],
                predicates=[{"path": "payload.key", "op": "eq", "value": key}],
                limit=1,
            )
            if result.items:
                existing = result.items[0]

        # Build learning object
        obj = {
            "envelope": {
                "type": "learning",
                "schema": "pcp.learning.v1",
                "tags": [category] if category else [],
                "lineage": {
                    "sources": [f"agent:{token.subject}"],
                    "confidence": confidence,
                    "parents": derived_from or [],
                },
            },
            "payload": {
                "key": key,
                "statement": statement,
                "confidence": confidence,
                "category": category,
                "derived_from": derived_from or [],
                "summary": f"{key}: {statement[:100]}",
            },
        }

        # Store or update
        if existing:
            obj_id = existing.get("envelope", {}).get("id")
            previous = existing.get("payload", {})
            self.storage.update(obj_id, obj)
        else:
            obj_id = self.storage.store(obj)
            previous = None

        # Log operation
        log_operation(
            operation="learn",
            requester=token.subject,
            token_id=token.token_id,
            object_types=[ObjectType.LEARNING],
            success=True,
        )

        return {
            "id": obj_id,
            "key": key,
            "previous": previous,
        }

    def reflect(
        self,
        token: Token,
        prompt: str,
        scope: str = "custom",
        horizon: dict[str, str] | None = None,
        context: list[str] | None = None,
        save: bool = False,
        replace_scope: bool = False,
    ) -> dict[str, Any]:
        """
        Generate a reflection over personal context using Claude.
        """
        # Validate scope
        validate_scope(token.scopes, Operation.REFLECT, ObjectType.REFLECTION)

        # Gather context from events and learnings
        context_parts = []
        source_ids = []

        if context is None or "events" in context:
            # Query recent events
            events = self.storage.query(
                object_types=[ObjectType.EVENT],
                disclosure=DisclosureLevel.SUMMARY,
                limit=50,
            )
            if events.items:
                context_parts.append("## Recent Activity")
                for item in events.items:
                    summary = item.get("payload", {}).get("summary", "")
                    if summary:
                        context_parts.append(f"- {summary}")
                    source_ids.append(item.get("envelope", {}).get("id", ""))

        if context is None or "learnings" in context:
            # Query learnings
            learnings = self.storage.query(
                object_types=[ObjectType.LEARNING],
                disclosure=DisclosureLevel.SUMMARY,
                limit=20,
            )
            if learnings.items:
                context_parts.append("\n## Known Facts")
                for item in learnings.items:
                    summary = item.get("payload", {}).get("summary", "")
                    if summary:
                        context_parts.append(f"- {summary}")
                    source_ids.append(item.get("envelope", {}).get("id", ""))

        context_text = "\n".join(context_parts) if context_parts else "No context available."

        # Try to generate reflection with Claude
        client = get_anthropic_client()
        if client:
            try:
                system_prompt = """You are a personal reflection assistant. Based on the user's activity and known facts, provide a thoughtful, concise reflection.

Be specific and reference actual events/patterns from the context. Keep the reflection to 2-3 paragraphs."""

                user_prompt = f"""Context from personal activity:

{context_text}

User's question: {prompt}

Provide a reflection based on this context."""

                response = client.messages.create(
                    model="claude-sonnet-4-20250514",
                    max_tokens=1024,
                    system=system_prompt,
                    messages=[{"role": "user", "content": user_prompt}],
                )
                content = response.content[0].text
            except Exception as e:
                content = f"[Reflection generation failed: {e}]\n\nContext summary:\n{context_text}"
        else:
            # Fallback without API key
            content = f"[No ANTHROPIC_API_KEY set - showing context]\n\n{context_text}"

        # Build reflection object
        reflection = {
            "envelope": {
                "type": "reflection",
                "schema": "pcp.reflection.v1",
                "tags": [f"scope:{scope}"],
                "lineage": {
                    "sources": [f"agent:{token.subject}"],
                    "parents": source_ids[:10],  # Link to source events/learnings
                },
            },
            "payload": {
                "scope": scope,
                "horizon": horizon or {},
                "summary": content[:200] + "..." if len(content) > 200 else content,
                "content": content,
                "detail": {
                    "prompt": prompt,
                    "context_types": context or ["events", "learnings"],
                    "source_count": len(source_ids),
                },
                "sources": source_ids[:20],
            },
        }

        obj_id = None
        if save:
            # If replace_scope, delete existing reflection for this scope/horizon
            if replace_scope and horizon:
                existing = self.storage.query(
                    object_types=[ObjectType.REFLECTION],
                    predicates=[
                        {"path": "payload.scope", "op": "eq", "value": scope},
                    ],
                    limit=1,
                )
                for item in existing.items:
                    self.storage.delete(item.get("envelope", {}).get("id", ""))

            obj_id = self.storage.store(reflection)

        # Log operation
        log_operation(
            operation="reflect",
            requester=token.subject,
            token_id=token.token_id,
            object_types=[ObjectType.REFLECTION],
            success=True,
        )

        return {
            "content": content,
            "sources": source_ids,
            "id": obj_id,
        }

    def _generate_summary(self, items: list[dict[str, Any]]) -> str:
        """Generate an LLM summary of items (placeholder)."""
        summaries = [
            item.get("payload", {}).get("summary", "")
            for item in items[:10]
        ]
        return "Summary: " + "; ".join(s for s in summaries if s)
