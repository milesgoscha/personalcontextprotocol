"""Reflection payload - synthesized summaries over time periods."""

from datetime import date
from typing import Any

from pydantic import BaseModel, Field


class Horizon(BaseModel):
    """Time range covered by the reflection."""

    start: date
    end: date


class Theme(BaseModel):
    """Identified theme with salience score."""

    label: str
    salience: float = Field(..., ge=0.0, le=1.0)


class ReflectionPayload(BaseModel):
    """
    Reflections are episodic or situational snapshots synthesized from
    events and learning trajectories.

    They're the output of the `reflect` operation - agents query context
    and produce reflections that capture patterns, themes, and insights.
    """

    scope: str = Field(..., description="Reflection scope (daily, weekly, custom)")
    horizon: Horizon = Field(..., description="Time range covered")

    # Progressive disclosure
    summary: str = Field(..., description="One-line summary (<500 chars)")
    content: str = Field(..., description="Full reflection text")
    detail: dict[str, Any] | None = Field(
        None,
        description="Structured breakdown (themes, open_questions, etc.)",
    )

    # Lineage
    sources: list[str] = Field(
        default_factory=list, description="Event/learning IDs that informed this"
    )
    raw_ref: str | None = Field(None, description="URI to raw generation trace")

    def to_summary(self) -> dict[str, Any]:
        """Return summary-level disclosure."""
        return {
            "scope": self.scope,
            "horizon": self.horizon.model_dump(),
            "summary": self.summary,
        }

    def to_detail(self) -> dict[str, Any]:
        """Return detail-level disclosure."""
        return {
            "scope": self.scope,
            "horizon": self.horizon.model_dump(),
            "summary": self.summary,
            "content": self.content,
            "detail": self.detail,
        }

    def to_raw(self) -> dict[str, Any]:
        """Return raw-level disclosure (includes sources, raw_ref)."""
        return self.model_dump()

    @property
    def themes(self) -> list[Theme]:
        """Extract themes from detail if present."""
        if self.detail and "themes" in self.detail:
            return [Theme(**t) for t in self.detail["themes"]]
        return []
