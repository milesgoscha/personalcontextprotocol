"""Tests for multi-tenant storage isolation."""

import tempfile
from pathlib import Path

import pytest

from pcp.auth.grants import GrantStore
from pcp.auth.tokens import TokenStore
from pcp.server.storage import Storage


class TestStorageMultiTenant:
    """Test Storage class multi-tenant isolation."""

    def test_user_isolation(self, temp_data_dir):
        """Data from user A not visible to user B."""
        storage_a = Storage(data_dir=temp_data_dir, user_id="user-a")
        storage_b = Storage(data_dir=temp_data_dir, user_id="user-b")

        # User A stores an object
        obj_id = storage_a.store({
            "envelope": {"type": "event"},
            "payload": {"summary": "User A's event"},
        })

        # User B cannot see it
        assert storage_b.get(obj_id) is None

        # User A can see it
        assert storage_a.get(obj_id) is not None

    def test_directory_structure(self, temp_data_dir):
        """Each user gets own subdirectory."""
        storage = Storage(data_dir=temp_data_dir, user_id="user-123")

        # Store something to ensure files are created
        storage.store({
            "envelope": {"type": "event"},
            "payload": {"summary": "Test"},
        })

        # Verify directory structure
        assert (temp_data_dir / "user-123").is_dir()
        assert (temp_data_dir / "user-123" / "objects.jsonl").exists()

    def test_single_tenant_unchanged(self, temp_data_dir):
        """Without user_id, uses root directory (backward compatible)."""
        storage = Storage(data_dir=temp_data_dir, user_id=None)
        assert storage.data_dir == temp_data_dir

    def test_tombstones_per_tenant(self, temp_data_dir):
        """Tombstones and compact() respect user directory."""
        storage = Storage(data_dir=temp_data_dir, user_id="user-a")

        # Store and delete
        obj_id = storage.store({
            "envelope": {"type": "event"},
            "payload": {"summary": "To be deleted"},
        })
        storage.delete(obj_id)

        # Compact
        removed = storage.compact()

        # Verify tombstone was in user-a directory only
        assert (temp_data_dir / "user-a" / "objects.jsonl").exists()
        # Root directory should not have objects.jsonl
        assert not (temp_data_dir / "objects.jsonl").exists()

    def test_identity_per_tenant(self, temp_data_dir):
        """Identity is scoped per user."""
        storage_a = Storage(data_dir=temp_data_dir, user_id="user-a")
        storage_b = Storage(data_dir=temp_data_dir, user_id="user-b")

        # Set identity for user A
        storage_a.set_identity({"name": "Alice", "email": "alice@example.com"})

        # User B should not see user A's identity
        assert storage_b.get_identity() is None

        # User A should see their identity
        identity_a = storage_a.get_identity()
        assert identity_a["name"] == "Alice"


class TestGrantStoreMultiTenant:
    """Test GrantStore multi-tenant isolation."""

    def test_grants_isolated(self, temp_data_dir):
        """Grants from user A not visible to user B."""
        store_a = GrantStore(data_dir=temp_data_dir, user_id="user-a")
        store_b = GrantStore(data_dir=temp_data_dir, user_id="user-b")

        # Create grant for user A
        grant, secret = store_a.create(
            client_id="client-1",
            client_name="Test Client",
            scopes_requested=["query:event.*"],
            reason="Testing",
        )

        # User B cannot see it
        assert store_b.get(grant.grant_id) is None
        assert len(store_b.list_grants()) == 0

        # User A can see it
        assert store_a.get(grant.grant_id) is not None
        assert len(store_a.list_grants()) == 1

    def test_grants_file_per_user(self, temp_data_dir):
        """Each user has own grants.json file."""
        store = GrantStore(data_dir=temp_data_dir, user_id="user-123")

        store.create(
            client_id="client-1",
            client_name="Test Client",
            scopes_requested=["query:event.*"],
            reason="Testing",
        )

        # Verify file location
        assert (temp_data_dir / "user-123" / "grants.json").exists()
        assert not (temp_data_dir / "grants.json").exists()


class TestTokenStoreMultiTenant:
    """Test TokenStore multi-tenant isolation."""

    def test_tokens_isolated(self, temp_data_dir):
        """Tokens from user A not visible to user B."""
        store_a = TokenStore(data_dir=temp_data_dir, user_id="user-a")
        store_b = TokenStore(data_dir=temp_data_dir, user_id="user-b")

        # Create token for user A
        token_string, token = store_a.create(
            subject="test-agent",
            scopes=["query:event.*"],
        )

        # User B cannot verify it (different signing key)
        assert store_b.verify(token_string) is None

        # User A can verify it
        assert store_a.verify(token_string) is not None

    def test_signing_key_per_user(self, temp_data_dir):
        """Each user has own signing key."""
        store_a = TokenStore(data_dir=temp_data_dir, user_id="user-a")
        store_b = TokenStore(data_dir=temp_data_dir, user_id="user-b")

        # Keys should be different
        assert store_a._secret_key != store_b._secret_key

        # Key files should be in user directories
        assert (temp_data_dir / "user-a" / "signing_key.bin").exists()
        assert (temp_data_dir / "user-b" / "signing_key.bin").exists()


class TestCrossUserAccess:
    """Test that cross-user access is properly prevented."""

    def test_query_isolation(self, temp_data_dir):
        """Query only returns current user's objects."""
        from pcp.models.envelope import ObjectType

        storage_a = Storage(data_dir=temp_data_dir, user_id="user-a")
        storage_b = Storage(data_dir=temp_data_dir, user_id="user-b")

        # Store events for both users
        storage_a.store({
            "envelope": {"type": "event"},
            "payload": {"summary": "User A event"},
        })
        storage_b.store({
            "envelope": {"type": "event"},
            "payload": {"summary": "User B event"},
        })

        # Query user A
        result_a = storage_a.query(object_types=[ObjectType.EVENT])
        assert len(result_a.items) == 1
        assert "User A" in result_a.items[0]["payload"]["summary"]

        # Query user B
        result_b = storage_b.query(object_types=[ObjectType.EVENT])
        assert len(result_b.items) == 1
        assert "User B" in result_b.items[0]["payload"]["summary"]

    def test_count_isolation(self, temp_data_dir):
        """Count only returns current user's objects."""
        storage_a = Storage(data_dir=temp_data_dir, user_id="user-a")
        storage_b = Storage(data_dir=temp_data_dir, user_id="user-b")

        # Store 3 events for user A
        for i in range(3):
            storage_a.store({
                "envelope": {"type": "event"},
                "payload": {"summary": f"User A event {i}"},
            })

        # Store 1 event for user B
        storage_b.store({
            "envelope": {"type": "event"},
            "payload": {"summary": "User B event"},
        })

        # Counts should be isolated
        assert storage_a.count() == 3
        assert storage_b.count() == 1
