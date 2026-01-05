"""Common envelope wrapper for all PCP objects."""

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class ObjectType(str, Enum):
    """PCP object types."""

    IDENTITY = "identity"
    EVENT = "event"
    LEARNING = "learning"
    REFLECTION = "reflection"


class VisibilityClassification(str, Enum):
    """Visibility classification levels."""

    PRIVATE = "private"
    SHARED = "shared"
    PUBLIC = "public"


class DisclosureLevel(str, Enum):
    """Progressive disclosure levels."""

    SUMMARY = "summary"
    DETAIL = "detail"
    RAW = "raw"


class Subject(BaseModel):
    """Subject (owner) of the PCP object."""

    id: str = Field(..., description="DID or unique identifier")
    display_name: str | None = Field(None, description="Human-readable name")


class Visibility(BaseModel):
    """Visibility and access control settings."""

    classification: VisibilityClassification = VisibilityClassification.PRIVATE
    allowed_scopes: list[str] = Field(
        default_factory=list, description="Scopes that can access this object"
    )


class Disclosure(BaseModel):
    """Progressive disclosure metadata."""

    available_levels: list[DisclosureLevel] = Field(
        default_factory=lambda: [DisclosureLevel.SUMMARY, DisclosureLevel.DETAIL]
    )
    default_level: DisclosureLevel = DisclosureLevel.SUMMARY


class Lineage(BaseModel):
    """Provenance and derivation information."""

    parents: list[str] = Field(default_factory=list, description="Parent object URIs")
    sources: list[str] = Field(
        default_factory=list, description="Source collectors/agents"
    )
    confidence: float = Field(1.0, ge=0.0, le=1.0, description="Confidence score")


class Attachment(BaseModel):
    """Binary attachment reference."""

    name: str
    mime: str
    size_bytes: int
    uri: str = Field(..., description="pcp:// URI to blob")
    hash: str | None = Field(None, description="Content hash (sha256)")


class Envelope(BaseModel):
    """
    Common envelope wrapper for all PCP objects.

    Every PCP object (identity, event, learning, reflection) is wrapped
    in this versioned envelope before storage and transmission.
    """

    id: str = Field(
        default_factory=lambda: f"pcp://local/obj/{uuid4().hex[:12]}",
        description="Canonical pcp:// URI",
    )
    type: ObjectType
    version: str = Field("0.1.0", description="PCP schema version")
    object_schema: str = Field(..., description="Object schema identifier (e.g., pcp.event.v1)", alias="schema")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    subject: Subject
    tags: list[str] = Field(default_factory=list)
    visibility: Visibility = Field(default_factory=Visibility)
    disclosure: Disclosure = Field(default_factory=Disclosure)
    lineage: Lineage = Field(default_factory=Lineage)
    attachments: list[Attachment] = Field(default_factory=list)
    extensions: dict[str, Any] = Field(
        default_factory=dict, description="Namespaced extension data (reverse-DNS keys)"
    )

    class Config:
        json_encoders = {datetime: lambda v: v.isoformat()}
        populate_by_name = True  # Accept both "schema" and "object_schema"


class PCPObject(BaseModel):
    """A complete PCP object with envelope and payload."""

    envelope: Envelope
    payload: dict[str, Any]

    # Response metadata (set by server, not stored)
    disclosure_level: DisclosureLevel | None = Field(
        None, description="Current disclosure level of this response"
    )
    detail_available: bool = Field(True, description="Whether detail level is available")
    raw_available: bool = Field(False, description="Whether raw level is available")
