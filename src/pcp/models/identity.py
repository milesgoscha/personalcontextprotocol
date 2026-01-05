"""Identity payload - stable, near-immutable facts about the user."""

from typing import Any

from pydantic import BaseModel, Field


class IdentityPayload(BaseModel):
    """
    Identity represents stable, near-immutable facts about the user.

    Most of "who you are" is learnings, not identity. Identity is reserved
    for truly stable facts like name, timezone, and locale.
    """

    name: str | None = Field(None, description="User's name")
    timezone: str | None = Field(None, description="IANA timezone (e.g., America/Los_Angeles)")
    locale: str | None = Field(None, description="Locale code (e.g., en-US)")
    did: str | None = Field(None, description="Decentralized identifier")

    # Progressive disclosure
    summary: str = Field(..., description="Brief description (<500 chars)")
    detail: dict[str, Any] | None = Field(
        None, description="Extended identity info (preferred_name, pronouns, etc.)"
    )

    # Extension hook
    custom: dict[str, Any] = Field(
        default_factory=dict,
        description="Domain-specific identity facts (reverse-DNS keys)",
    )

    def to_summary(self) -> dict[str, Any]:
        """Return summary-level disclosure."""
        return {"summary": self.summary}

    def to_detail(self) -> dict[str, Any]:
        """Return detail-level disclosure."""
        return {
            "name": self.name,
            "timezone": self.timezone,
            "locale": self.locale,
            "did": self.did,
            "summary": self.summary,
            "detail": self.detail,
        }

    def to_raw(self) -> dict[str, Any]:
        """Return raw-level disclosure (includes custom extensions)."""
        return self.model_dump()
