"""Learning payload - durable, queryable facts about the user."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ValidFor(BaseModel):
    """Time range during which this learning is valid."""

    start: datetime
    end: datetime | None = None  # None means "still valid"


class LearningPayload(BaseModel):
    """
    Learnings are durable, queryable facts that agents can rely on until revoked.

    Unlike identity (near-immutable), learnings evolve over time as agents
    observe patterns and update their understanding of the user.
    """

    key: str = Field(..., description="Unique key for this learning (e.g., preferred_ide)")
    statement: str = Field(..., description="Human-readable statement of the learning")
    confidence: float = Field(1.0, ge=0.0, le=1.0, description="Confidence score")
    category: str | None = Field(
        None, description="Category (preferences, patterns, facts)"
    )
    derived_from: list[str] = Field(
        default_factory=list, description="Source event/learning IDs"
    )
    valid_for: ValidFor | None = Field(
        None, description="Time range this learning applies to"
    )

    # Progressive disclosure
    summary: str = Field(..., description="Brief description (<500 chars)")
    detail: dict[str, Any] | None = Field(
        None,
        description="Supporting evidence (evidence_snippets, supporting_metrics)",
    )

    def to_summary(self) -> dict[str, Any]:
        """Return summary-level disclosure."""
        return {
            "key": self.key,
            "summary": self.summary,
            "confidence": self.confidence,
        }

    def to_detail(self) -> dict[str, Any]:
        """Return detail-level disclosure."""
        return {
            "key": self.key,
            "statement": self.statement,
            "confidence": self.confidence,
            "category": self.category,
            "summary": self.summary,
            "detail": self.detail,
        }

    def to_raw(self) -> dict[str, Any]:
        """Return raw-level disclosure (includes derived_from, valid_for)."""
        return self.model_dump()
