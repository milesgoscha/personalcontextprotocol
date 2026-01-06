"""Shared test fixtures for PCP tests."""

import tempfile
from datetime import timedelta
from pathlib import Path

import pytest

from pcp.auth.grants import GrantStore, TrustTier
from pcp.auth.tokens import TokenStore
from pcp.server.operations import PCPOperations
from pcp.server.storage import Storage


@pytest.fixture
def temp_data_dir():
    """Create a temporary data directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def storage(temp_data_dir):
    """Create a Storage instance with temp directory."""
    return Storage(data_dir=temp_data_dir)


@pytest.fixture
def token_store(temp_data_dir):
    """Create a TokenStore instance with temp directory."""
    return TokenStore(data_dir=temp_data_dir)


@pytest.fixture
def grant_store(temp_data_dir):
    """Create a GrantStore instance with temp directory."""
    return GrantStore(data_dir=temp_data_dir)


@pytest.fixture
def operations(storage):
    """Create PCPOperations with storage."""
    return PCPOperations(storage=storage, node_id="pcp://test")


@pytest.fixture
def admin_token(token_store):
    """Create an admin token."""
    token_string, token = token_store.create(
        subject="test-admin",
        scopes=["pcp:admin", "query:event.*", "observe:event", "learn:write", "reflect:write"],
        expires_in=timedelta(hours=1),
        metadata={"trust_tier": "local"},
    )
    return token_string, token


@pytest.fixture
def third_party_token(token_store):
    """Create a third-party token with limited scopes."""
    token_string, token = token_store.create(
        subject="test-third-party",
        scopes=["query:event.summary"],
        expires_in=timedelta(hours=1),
        metadata={"trust_tier": "third_party"},
    )
    return token_string, token


@pytest.fixture
def sample_events(storage):
    """Create sample events in storage."""
    events = []
    for i in range(5):
        obj_id = storage.store({
            "envelope": {
                "type": "event",
                "tags": ["test", f"batch-{i % 2}"],
            },
            "payload": {
                "event_kind": "test_event",
                "summary": f"Test event {i}",
                "detail": {"index": i},
            },
        })
        events.append(obj_id)
    return events
