"""Tests for token persistence."""

import tempfile
from datetime import timedelta
from pathlib import Path

import pytest

from pcp.auth.tokens import TokenStore


@pytest.fixture
def token_store():
    """Create a temporary token store."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield TokenStore(data_dir=Path(tmpdir))


class TestTokenPersistence:
    def test_create_and_verify_token(self, token_store):
        """Basic token creation and verification."""
        token_string, token = token_store.create(
            subject="test-agent",
            scopes=["query:event.*"],
            expires_in=timedelta(hours=1),
        )

        assert token_string.startswith("pcp_")
        assert token.subject == "test-agent"

        # Verify works
        verified = token_store.verify(token_string)
        assert verified is not None
        assert verified.token_id == token.token_id

    def test_signing_key_persists(self):
        """Signing key persists across store instances."""
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)

            # Create first store and a token
            store1 = TokenStore(data_dir=data_dir)
            token_string, token = store1.create(
                subject="test-agent",
                scopes=["query:event.*"],
            )

            # Create second store (simulates restart)
            store2 = TokenStore(data_dir=data_dir)

            # Token should still verify with new store
            verified = store2.verify(token_string)
            assert verified is not None
            assert verified.token_id == token.token_id

    def test_tokens_persist_across_restarts(self):
        """Tokens survive store recreation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)

            # Create store and tokens
            store1 = TokenStore(data_dir=data_dir)
            token_string1, _ = store1.create(subject="agent1", scopes=["query:event.*"])
            token_string2, _ = store1.create(subject="agent2", scopes=["observe:event"])

            # Recreate store (simulates restart)
            store2 = TokenStore(data_dir=data_dir)

            # Both tokens should still work
            assert store2.verify(token_string1) is not None
            assert store2.verify(token_string2) is not None
            assert len(store2.list_tokens()) == 2

    def test_revoked_token_persists(self):
        """Revocation persists across restarts."""
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)

            # Create and revoke token
            store1 = TokenStore(data_dir=data_dir)
            token_string, token = store1.create(subject="agent", scopes=["query:event.*"])
            store1.revoke(token.token_id)

            # Recreate store
            store2 = TokenStore(data_dir=data_dir)

            # Token should not verify
            assert store2.verify(token_string) is None
            assert len(store2.list_tokens()) == 0

    def test_expired_tokens_cleaned_on_load(self):
        """Expired tokens are not loaded."""
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)

            # Create token that expires immediately
            store1 = TokenStore(data_dir=data_dir)
            token_string, _ = store1.create(
                subject="agent",
                scopes=["query:event.*"],
                expires_in=timedelta(seconds=-1),  # Already expired
            )

            # Recreate store
            store2 = TokenStore(data_dir=data_dir)

            # Expired token should not be loaded
            assert store2.verify(token_string) is None
            assert len(store2.list_tokens()) == 0

    def test_signing_key_file_permissions(self):
        """Signing key file has restricted permissions."""
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            store = TokenStore(data_dir=data_dir)

            key_file = data_dir / "signing_key.bin"
            assert key_file.exists()

            # Check permissions (owner read/write only)
            import stat
            mode = key_file.stat().st_mode
            assert mode & stat.S_IRWXG == 0  # No group permissions
            assert mode & stat.S_IRWXO == 0  # No other permissions

    def test_metadata_persists(self):
        """Token metadata survives restart."""
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)

            store1 = TokenStore(data_dir=data_dir)
            token_string, _ = store1.create(
                subject="agent",
                scopes=["query:event.*"],
                metadata={"trust_tier": "third_party", "grant_id": "gr_123"},
            )

            store2 = TokenStore(data_dir=data_dir)
            verified = store2.verify(token_string)

            assert verified is not None
            assert verified.metadata["trust_tier"] == "third_party"
            assert verified.metadata["grant_id"] == "gr_123"
