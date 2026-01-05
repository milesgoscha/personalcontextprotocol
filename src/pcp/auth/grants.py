"""
PCP Grant management.

Grants represent pending or approved access requests from agents.
This implements the OAuth-style consent flow for third-party agents.
"""

import hashlib
import json
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any

from .tokens import create_token


def _hash_secret(secret: str) -> str:
    """Hash a claim secret for storage."""
    return hashlib.sha256(secret.encode()).hexdigest()


def _verify_secret(secret: str, secret_hash: str) -> bool:
    """Verify a claim secret against its hash."""
    return hashlib.sha256(secret.encode()).hexdigest() == secret_hash


class GrantStatus(str, Enum):
    """Grant lifecycle states."""

    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    REVOKED = "revoked"
    EXPIRED = "expired"


class TrustTier(str, Enum):
    """Trust tier classification."""

    LOCAL = "local"
    FIRST_PARTY_REMOTE = "first_party_remote"
    THIRD_PARTY = "third_party"


# Default settings per trust tier
TIER_DEFAULTS = {
    TrustTier.LOCAL: {
        "disclosure_max": "raw",
        "token_lifetime_hours": 24,
        "auto_approve": True,
        "redactions": [],
    },
    TrustTier.FIRST_PARTY_REMOTE: {
        "disclosure_max": "detail",
        "token_lifetime_hours": 8,
        "auto_approve": False,
        "redactions": ["payload.detail.url"],
    },
    TrustTier.THIRD_PARTY: {
        "disclosure_max": "summary",
        "token_lifetime_hours": 1,
        "auto_approve": False,
        "redactions": ["payload.detail.*", "envelope.lineage.*"],
    },
}


@dataclass
class Grant:
    """A grant request/approval record."""

    grant_id: str
    client_id: str
    client_name: str
    scopes_requested: list[str]
    scopes_approved: list[str] | None
    reason: str
    callback_url: str | None
    trust_tier: TrustTier
    status: GrantStatus
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None
    denial_reason: str | None = None
    token_id: str | None = None  # Set when token is issued
    claim_secret_hash: str | None = None  # Hash of secret required to claim token
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self, include_secret_hash: bool = False) -> dict[str, Any]:
        result = {
            "grant_id": self.grant_id,
            "client_id": self.client_id,
            "client_name": self.client_name,
            "scopes_requested": self.scopes_requested,
            "scopes_approved": self.scopes_approved,
            "reason": self.reason,
            "callback_url": self.callback_url,
            "trust_tier": self.trust_tier.value,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "denial_reason": self.denial_reason,
            "token_id": self.token_id,
            "metadata": self.metadata,
        }
        # Only include secret hash for internal storage, never in API responses
        if include_secret_hash:
            result["claim_secret_hash"] = self.claim_secret_hash
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Grant":
        return cls(
            grant_id=data["grant_id"],
            client_id=data["client_id"],
            client_name=data["client_name"],
            scopes_requested=data["scopes_requested"],
            scopes_approved=data.get("scopes_approved"),
            reason=data["reason"],
            callback_url=data.get("callback_url"),
            trust_tier=TrustTier(data["trust_tier"]),
            status=GrantStatus(data["status"]),
            created_at=datetime.fromisoformat(data["created_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
            expires_at=datetime.fromisoformat(data["expires_at"]) if data.get("expires_at") else None,
            denial_reason=data.get("denial_reason"),
            token_id=data.get("token_id"),
            claim_secret_hash=data.get("claim_secret_hash"),
            metadata=data.get("metadata", {}),
        )


class GrantStore:
    """Persistent storage for grants."""

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.grants_file = data_dir / "grants.json"
        self._grants: dict[str, Grant] = {}
        self._load()

    def _load(self) -> None:
        """Load grants from disk."""
        if self.grants_file.exists():
            with open(self.grants_file) as f:
                data = json.load(f)
                for grant_data in data.get("grants", []):
                    grant = Grant.from_dict(grant_data)
                    self._grants[grant.grant_id] = grant

    def _save(self) -> None:
        """Save grants to disk."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        with open(self.grants_file, "w") as f:
            json.dump(
                {"grants": [g.to_dict(include_secret_hash=True) for g in self._grants.values()]},
                f,
                indent=2,
            )

    def create(
        self,
        client_id: str,
        client_name: str,
        scopes_requested: list[str],
        reason: str,
        callback_url: str | None = None,
        trust_tier: TrustTier = TrustTier.THIRD_PARTY,
    ) -> tuple[Grant, str]:
        """
        Create a new grant request.

        Returns:
            Tuple of (Grant, claim_secret). The claim_secret must be stored
            by the client and provided when claiming the token.
        """
        grant_id = f"gr_{secrets.token_hex(8)}"
        now = datetime.utcnow()

        # Generate claim secret for token redemption
        claim_secret = secrets.token_urlsafe(32)
        claim_secret_hash = _hash_secret(claim_secret)

        # Check if auto-approve applies
        tier_defaults = TIER_DEFAULTS[trust_tier]
        status = GrantStatus.APPROVED if tier_defaults["auto_approve"] else GrantStatus.PENDING

        grant = Grant(
            grant_id=grant_id,
            client_id=client_id,
            client_name=client_name,
            scopes_requested=scopes_requested,
            scopes_approved=scopes_requested if status == GrantStatus.APPROVED else None,
            reason=reason,
            callback_url=callback_url,
            trust_tier=trust_tier,
            status=status,
            created_at=now,
            updated_at=now,
            expires_at=None,
            claim_secret_hash=claim_secret_hash,
        )

        self._grants[grant_id] = grant
        self._save()
        return grant, claim_secret

    def get(self, grant_id: str) -> Grant | None:
        """Get a grant by ID."""
        return self._grants.get(grant_id)

    def list_grants(
        self,
        status: GrantStatus | None = None,
        client_id: str | None = None,
    ) -> list[Grant]:
        """List grants with optional filtering."""
        grants = list(self._grants.values())

        if status:
            grants = [g for g in grants if g.status == status]
        if client_id:
            grants = [g for g in grants if g.client_id == client_id]

        return sorted(grants, key=lambda g: g.created_at, reverse=True)

    def approve(
        self,
        grant_id: str,
        scopes: list[str] | None = None,
        lifetime_hours: int | None = None,
    ) -> Grant | None:
        """Approve a grant, optionally modifying scopes."""
        grant = self._grants.get(grant_id)
        if not grant or grant.status != GrantStatus.PENDING:
            return None

        # Use requested scopes if not overridden
        grant.scopes_approved = scopes or grant.scopes_requested
        grant.status = GrantStatus.APPROVED
        grant.updated_at = datetime.utcnow()

        # Set token expiry based on tier defaults or override
        tier_defaults = TIER_DEFAULTS[grant.trust_tier]
        hours = lifetime_hours or tier_defaults["token_lifetime_hours"]
        grant.expires_at = datetime.utcnow() + timedelta(hours=hours)

        self._save()
        return grant

    def deny(self, grant_id: str, reason: str | None = None) -> Grant | None:
        """Deny a grant request."""
        grant = self._grants.get(grant_id)
        if not grant or grant.status != GrantStatus.PENDING:
            return None

        grant.status = GrantStatus.DENIED
        grant.denial_reason = reason
        grant.updated_at = datetime.utcnow()

        self._save()
        return grant

    def revoke(self, grant_id: str) -> Grant | None:
        """Revoke an approved grant."""
        grant = self._grants.get(grant_id)
        if not grant or grant.status != GrantStatus.APPROVED:
            return None

        grant.status = GrantStatus.REVOKED
        grant.updated_at = datetime.utcnow()

        self._save()
        return grant

    def verify_claim_secret(self, grant_id: str, claim_secret: str) -> bool:
        """Verify the claim secret for a grant."""
        grant = self._grants.get(grant_id)
        if not grant or not grant.claim_secret_hash:
            return False
        return _verify_secret(claim_secret, grant.claim_secret_hash)

    def issue_token(self, grant_id: str, claim_secret: str | None = None) -> tuple[str, Grant] | None:
        """
        Issue a token for an approved grant.

        Args:
            grant_id: The grant ID
            claim_secret: The claim secret provided during grant creation.
                         Required for non-local trust tiers.

        Returns:
            Tuple of (token_string, Grant) if successful, None otherwise.
        """
        grant = self._grants.get(grant_id)
        if not grant or grant.status != GrantStatus.APPROVED:
            return None

        if not grant.scopes_approved:
            return None

        # Verify claim secret for non-local tiers
        if grant.trust_tier != TrustTier.LOCAL:
            if not claim_secret or not self.verify_claim_secret(grant_id, claim_secret):
                return None

        # Calculate remaining time
        if grant.expires_at:
            remaining = grant.expires_at - datetime.utcnow()
            if remaining.total_seconds() <= 0:
                grant.status = GrantStatus.EXPIRED
                self._save()
                return None
        else:
            # Use tier default
            tier_defaults = TIER_DEFAULTS[grant.trust_tier]
            remaining = timedelta(hours=tier_defaults["token_lifetime_hours"])

        # Create token with grant metadata
        token_string, token = create_token(
            subject=grant.client_id,
            scopes=grant.scopes_approved,
            expires_in=remaining,
            metadata={
                "grant_id": grant.grant_id,
                "trust_tier": grant.trust_tier.value,
                "client_name": grant.client_name,
            },
        )

        grant.token_id = token.token_id
        grant.updated_at = datetime.utcnow()
        self._save()

        return token_string, grant
