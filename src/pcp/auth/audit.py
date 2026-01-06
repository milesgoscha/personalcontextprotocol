"""
PCP Audit logging.

Every operation is logged as a PCP event under the reserved pcp.audit.* namespace.
Audit events are immutable and form the basis for compliance and debugging.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import uuid4

from pcp.models.envelope import DisclosureLevel, ObjectType


@dataclass
class AuditEvent:
    """An audit log entry (stored as a PCP event)."""

    id: str = field(default_factory=lambda: f"pcp://local/audit/{uuid4().hex[:12]}")
    timestamp: datetime = field(default_factory=datetime.utcnow)
    event_kind: str = "pcp.audit.operation"

    # Who
    requester: str = ""  # Token subject or "anonymous"
    token_id: str | None = None

    # What
    operation: str = ""  # describe, query, observe, learn, reflect
    object_types: list[str] = field(default_factory=list)
    disclosure_level: str = "summary"

    # Request details
    request_filter: dict[str, Any] | None = None
    request_params: dict[str, Any] | None = None

    # Result
    success: bool = True
    error: str | None = None
    result_count: int | None = None

    # Context
    ip_address: str | None = None
    user_agent: str | None = None

    def to_pcp_event(self) -> dict[str, Any]:
        """Convert to PCP event format for storage."""
        return {
            "envelope": {
                "id": self.id,
                "type": "event",
                "schema": "pcp.audit.v1",
                "created_at": self.timestamp.isoformat(),
                "tags": ["pcp.audit", f"pcp.audit.{self.operation}"],
                "visibility": {
                    "classification": "private",
                    "allowed_scopes": ["pcp:admin"],
                },
            },
            "payload": {
                "event_kind": self.event_kind,
                "timestamp": self.timestamp.isoformat(),
                "summary": f"{self.operation} by {self.requester}",
                "detail": {
                    "requester": self.requester,
                    "token_id": self.token_id,
                    "operation": self.operation,
                    "object_types": self.object_types,
                    "disclosure_level": self.disclosure_level,
                    "request_filter": self.request_filter,
                    "success": self.success,
                    "error": self.error,
                    "result_count": self.result_count,
                    "ip_address": self.ip_address,
                    "user_agent": self.user_agent,
                },
            },
        }


class AuditLog:
    """
    Audit log manager.

    In production, this would write to an append-only store.
    For now, keeps events in memory with optional file persistence.

    In multi-tenant mode, pass user_id and data_dir to scope audit logs
    to that user's directory.
    """

    def __init__(
        self,
        persist_path: str | None = None,
        data_dir: str | None = None,
        user_id: str | None = None,
    ):
        self.events: list[AuditEvent] = []

        # In multi-tenant mode, scope audit log to user's directory
        if data_dir and user_id:
            from pathlib import Path
            user_dir = Path(data_dir) / user_id
            user_dir.mkdir(parents=True, exist_ok=True)
            self.persist_path = str(user_dir / "audit.jsonl")
        else:
            self.persist_path = persist_path

    def log(
        self,
        operation: str,
        requester: str,
        token_id: str | None = None,
        object_types: list[ObjectType] | None = None,
        disclosure_level: DisclosureLevel = DisclosureLevel.SUMMARY,
        request_filter: dict[str, Any] | None = None,
        request_params: dict[str, Any] | None = None,
        success: bool = True,
        error: str | None = None,
        result_count: int | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> AuditEvent:
        """Log an operation."""
        event = AuditEvent(
            requester=requester,
            token_id=token_id,
            operation=operation,
            object_types=[ot.value for ot in (object_types or [])],
            disclosure_level=disclosure_level.value,
            request_filter=request_filter,
            request_params=request_params,
            success=success,
            error=error,
            result_count=result_count,
            ip_address=ip_address,
            user_agent=user_agent,
        )

        self.events.append(event)

        # Persist if configured
        if self.persist_path:
            self._persist(event)

        return event

    def _persist(self, event: AuditEvent) -> None:
        """Append event to persistent store."""
        import json

        with open(self.persist_path, "a") as f:
            f.write(json.dumps(event.to_pcp_event()) + "\n")

    def query(
        self,
        operation: str | None = None,
        requester: str | None = None,
        since: datetime | None = None,
        limit: int = 100,
    ) -> list[AuditEvent]:
        """Query audit events."""
        results = self.events

        if operation:
            results = [e for e in results if e.operation == operation]
        if requester:
            results = [e for e in results if e.requester == requester]
        if since:
            results = [e for e in results if e.timestamp >= since]

        return sorted(results, key=lambda e: e.timestamp, reverse=True)[:limit]

    def count_by_requester(self, since: datetime | None = None) -> dict[str, int]:
        """Count operations by requester."""
        counts: dict[str, int] = {}
        for event in self.events:
            if since and event.timestamp < since:
                continue
            counts[event.requester] = counts.get(event.requester, 0) + 1
        return counts


# Global audit log instance
_audit_log = AuditLog()


def get_audit_log() -> AuditLog:
    """Get the global audit log instance."""
    return _audit_log


def log_operation(**kwargs: Any) -> AuditEvent:
    """Convenience function to log an operation."""
    return _audit_log.log(**kwargs)
