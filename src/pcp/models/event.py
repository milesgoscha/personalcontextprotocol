"""Event payload - atomic observations from collectors."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class RawRef(BaseModel):
    """Reference to raw binary data."""

    uri: str = Field(..., description="pcp:// URI to raw blob")
    encoding: str = Field("binary", description="Encoding type")
    offsets: list[dict[str, int]] | None = Field(
        None, description="Byte offset ranges for partial fetch"
    )


class EventPayload(BaseModel):
    """
    Events capture atomic observations from collectors.

    Events are the raw signal that feeds into learnings and reflections.
    They're designed for high-volume ingestion with progressive disclosure.
    """

    event_kind: str = Field(
        ..., description="Event type (e.g., application.navigation, input.keystroke)"
    )
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    actor: str | None = Field(
        None, description="pcp:// URI of the device/process that generated this"
    )

    # Progressive disclosure
    summary: str = Field(..., description="Brief description (<500 chars)")
    detail: dict[str, Any] | None = Field(
        None,
        description="Structured payload (application, window_title, url, etc.)",
    )
    raw_ref: RawRef | None = Field(
        None, description="Reference to raw binary data"
    )

    def to_summary(self) -> dict[str, Any]:
        """Return summary-level disclosure."""
        return {
            "event_kind": self.event_kind,
            "timestamp": self.timestamp.isoformat(),
            "summary": self.summary,
        }

    def to_detail(self) -> dict[str, Any]:
        """Return detail-level disclosure."""
        return {
            "event_kind": self.event_kind,
            "timestamp": self.timestamp.isoformat(),
            "actor": self.actor,
            "summary": self.summary,
            "detail": self.detail,
        }

    def to_raw(self) -> dict[str, Any]:
        """Return raw-level disclosure (includes raw_ref)."""
        return self.model_dump()

    class Config:
        json_encoders = {datetime: lambda v: v.isoformat()}
