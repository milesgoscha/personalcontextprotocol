"""
PCP Token generation and verification.

Tokens are time-bounded, scope-limited credentials that agents use
to authenticate with PCP nodes.
"""

import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .scopes import ScopeSet


@dataclass
class Token:
    """A PCP access token."""

    token_id: str
    subject: str  # DID or agent identifier
    scopes: ScopeSet
    issued_at: datetime
    expires_at: datetime
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_expired(self) -> bool:
        return datetime.utcnow() > self.expires_at

    @property
    def is_valid(self) -> bool:
        return not self.is_expired

    @property
    def trust_tier(self) -> str:
        """Get the trust tier from metadata, defaulting to 'local'."""
        return self.metadata.get("trust_tier", "local")

    def to_dict(self) -> dict[str, Any]:
        return {
            "token_id": self.token_id,
            "subject": self.subject,
            "scopes": [str(s) for s in self.scopes],
            "issued_at": self.issued_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Token":
        return cls(
            token_id=data["token_id"],
            subject=data["subject"],
            scopes=ScopeSet.from_strings(data["scopes"]),
            issued_at=datetime.fromisoformat(data["issued_at"]),
            expires_at=datetime.fromisoformat(data["expires_at"]),
            metadata=data.get("metadata", {}),
        )


class TokenStore:
    """
    Persistent token storage.

    Stores tokens and the signing key to disk so they survive restarts.
    """

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.tokens_file = self.data_dir / "tokens.json"
        self.key_file = self.data_dir / "signing_key.bin"
        self._tokens: dict[str, Token] = {}
        self._secret_key: bytes = b""
        self._load()

    def _load(self) -> None:
        """Load tokens and signing key from disk."""
        self.data_dir.mkdir(parents=True, exist_ok=True)

        # Load or generate signing key
        if self.key_file.exists():
            self._secret_key = self.key_file.read_bytes()
        else:
            self._secret_key = secrets.token_bytes(32)
            self.key_file.write_bytes(self._secret_key)
            # Restrict permissions on key file
            self.key_file.chmod(0o600)

        # Load tokens
        if self.tokens_file.exists():
            with open(self.tokens_file) as f:
                data = json.load(f)
                for token_data in data.get("tokens", []):
                    try:
                        token = Token.from_dict(token_data)
                        # Skip expired tokens on load
                        if not token.is_expired:
                            self._tokens[token.token_id] = token
                    except (KeyError, ValueError):
                        # Skip malformed tokens
                        pass

    def _save(self) -> None:
        """Save tokens to disk."""
        self.data_dir.mkdir(parents=True, exist_ok=True)

        # Clean up expired tokens before saving
        self._cleanup_expired()

        with open(self.tokens_file, "w") as f:
            json.dump(
                {"tokens": [t.to_dict() for t in self._tokens.values()]},
                f,
                indent=2,
            )

    def _cleanup_expired(self) -> int:
        """Remove expired tokens. Returns count of removed tokens."""
        expired = [tid for tid, t in self._tokens.items() if t.is_expired]
        for tid in expired:
            del self._tokens[tid]
        return len(expired)

    def create(
        self,
        subject: str,
        scopes: list[str],
        expires_in: timedelta = timedelta(hours=1),
        metadata: dict[str, Any] | None = None,
    ) -> tuple[str, Token]:
        """
        Create a new PCP access token.

        Args:
            subject: DID or agent identifier
            scopes: List of scope strings (e.g., ["query:event.summary"])
            expires_in: Token lifetime
            metadata: Optional metadata to attach

        Returns:
            Tuple of (token_string, Token object)
        """
        token_id = secrets.token_hex(12)
        now = datetime.utcnow()

        token = Token(
            token_id=token_id,
            subject=subject,
            scopes=ScopeSet.from_strings(scopes),
            issued_at=now,
            expires_at=now + expires_in,
            metadata=metadata or {},
        )

        # Generate the token string (signed)
        payload = json.dumps(
            {
                "tid": token_id,
                "sub": subject,
                "exp": token.expires_at.timestamp(),
            },
            sort_keys=True,
        )
        signature = hmac.new(self._secret_key, payload.encode(), hashlib.sha256).hexdigest()[:16]
        token_string = f"pcp_{token_id}_{signature}"

        # Store and persist
        self._tokens[token_id] = token
        self._save()

        return token_string, token

    def verify(self, token_string: str) -> Token | None:
        """
        Verify a token string and return the Token object if valid.

        Returns None if token is invalid or expired.
        """
        if not token_string.startswith("pcp_"):
            return None

        parts = token_string.split("_")
        if len(parts) != 3:
            return None

        _, token_id, provided_sig = parts

        # Look up token
        token = self._tokens.get(token_id)
        if token is None:
            return None

        # Verify signature
        payload = json.dumps(
            {
                "tid": token_id,
                "sub": token.subject,
                "exp": token.expires_at.timestamp(),
            },
            sort_keys=True,
        )
        expected_sig = hmac.new(self._secret_key, payload.encode(), hashlib.sha256).hexdigest()[:16]

        if not hmac.compare_digest(provided_sig, expected_sig):
            return None

        # Check expiration
        if token.is_expired:
            return None

        return token

    def revoke(self, token_id: str) -> bool:
        """Revoke a token by ID."""
        if token_id in self._tokens:
            del self._tokens[token_id]
            self._save()
            return True
        return False

    def list_tokens(self, subject: str | None = None) -> list[Token]:
        """List all tokens, optionally filtered by subject."""
        # Clean up expired on list
        self._cleanup_expired()

        tokens = list(self._tokens.values())
        if subject:
            tokens = [t for t in tokens if t.subject == subject]
        return tokens

    def get(self, token_id: str) -> Token | None:
        """Get a token by ID."""
        token = self._tokens.get(token_id)
        if token and token.is_expired:
            return None
        return token


# Global token store instance (initialized lazily)
_token_store: TokenStore | None = None


def _get_store() -> TokenStore:
    """Get or initialize the global token store."""
    global _token_store
    if _token_store is None:
        # Default to ~/.pcp/data for backwards compatibility
        data_dir = Path.home() / ".pcp" / "data"
        _token_store = TokenStore(data_dir)
    return _token_store


def init_token_store(data_dir: Path) -> TokenStore:
    """Initialize the token store with a specific data directory."""
    global _token_store
    _token_store = TokenStore(data_dir)
    return _token_store


def get_token_store() -> TokenStore:
    """Get the current token store instance."""
    return _get_store()


# Convenience functions that delegate to the global store
# (maintains backwards compatibility with existing code)

def create_token(
    subject: str,
    scopes: list[str],
    expires_in: timedelta = timedelta(hours=1),
    metadata: dict[str, Any] | None = None,
) -> tuple[str, Token]:
    """Create a new PCP access token."""
    return _get_store().create(subject, scopes, expires_in, metadata)


def verify_token(token_string: str) -> Token | None:
    """Verify a token string and return the Token object if valid."""
    return _get_store().verify(token_string)


def revoke_token(token_id: str) -> bool:
    """Revoke a token by ID."""
    return _get_store().revoke(token_id)


def list_tokens(subject: str | None = None) -> list[Token]:
    """List all tokens, optionally filtered by subject."""
    return _get_store().list_tokens(subject)
