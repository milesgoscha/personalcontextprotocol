"""Tests for PCP operations (query, observe, learn)."""

import pytest
from datetime import datetime, timedelta

from pcp.auth.tokens import Token
from pcp.auth.scopes import ScopeSet
from pcp.server.operations import PCPOperations


def make_token(scopes: list[str], trust_tier: str = "local") -> Token:
    """Create a test token."""
    return Token(
        token_id="test-token",
        subject="test-agent",
        scopes=ScopeSet.from_strings(scopes),
        issued_at=datetime.utcnow(),
        expires_at=datetime.utcnow() + timedelta(hours=1),
        metadata={"trust_tier": trust_tier},
    )


class TestDescribe:
    def test_describe_returns_capabilities(self, operations):
        """Describe returns node capabilities."""
        token = make_token(["query:event.summary"])
        result = operations.describe(token)

        assert "node_id" in result
        assert result["node_id"] == "pcp://test"
        assert "schema_versions" in result
        assert "auth" in result

    def test_describe_without_token(self, operations):
        """Describe works without a token."""
        result = operations.describe(None)

        assert "node_id" in result
        assert result["node_id"] == "pcp://test"


class TestQuery:
    def test_query_requires_scope(self, operations, sample_events):
        """Query fails without appropriate scope."""
        token = make_token(["observe:event"])  # Wrong scope

        with pytest.raises(ValueError, match="[Ss]cope"):
            operations.query(
                token=token,
                object_types=["event"],
            )

    def test_query_with_valid_scope(self, operations, sample_events):
        """Query succeeds with valid scope."""
        token = make_token(["query:event.*"])

        result = operations.query(
            token=token,
            object_types=["event"],
        )

        assert result["count"] == 5

    def test_query_with_summary_scope(self, operations, sample_events):
        """Query with summary scope returns only summaries."""
        token = make_token(["query:event.summary"])

        result = operations.query(
            token=token,
            object_types=["event"],
        )

        assert result["count"] == 5
        # All items should have summary disclosure
        for item in result["items"]:
            assert item.get("disclosure_level") == "summary"

    def test_query_with_limit(self, operations, sample_events):
        """Query respects limit."""
        token = make_token(["query:event.*"])

        result = operations.query(
            token=token,
            object_types=["event"],
            limit=2,
        )

        assert len(result["items"]) == 2
        assert result["count"] == 5  # Total count


class TestObserve:
    def test_observe_stores_event(self, operations, storage):
        """Observe stores events."""
        token = make_token(["observe:event"])

        result = operations.observe(
            token=token,
            objects=[{
                "envelope": {"type": "event"},
                "payload": {
                    "event_kind": "test",
                    "summary": "Test event",
                },
            }],
        )

        assert result["count"] == 1
        assert len(result["ids"]) == 1

        # Verify stored
        stored = storage.get(result["ids"][0])
        assert stored is not None
        assert stored["payload"]["summary"] == "Test event"

    def test_observe_requires_scope(self, operations):
        """Observe fails without scope."""
        token = make_token(["query:event.*"])  # Wrong scope

        with pytest.raises(ValueError, match="[Ss]cope"):
            operations.observe(
                token=token,
                objects=[{"envelope": {"type": "event"}, "payload": {}}],
            )

    def test_observe_multiple_events(self, operations, storage):
        """Observe can store multiple events."""
        token = make_token(["observe:event"])

        events = [
            {
                "envelope": {"type": "event"},
                "payload": {"event_kind": "test", "summary": f"Event {i}"},
            }
            for i in range(3)
        ]

        result = operations.observe(token=token, objects=events)

        assert result["count"] == 3
        assert len(result["ids"]) == 3


class TestLearn:
    def test_learn_stores_learning(self, operations, storage):
        """Learn stores a learning."""
        token = make_token(["learn:write"])

        result = operations.learn(
            token=token,
            key="test_key",
            statement="Test statement",
        )

        assert "id" in result
        assert result["key"] == "test_key"

    def test_learn_requires_scope(self, operations):
        """Learn fails without scope."""
        token = make_token(["query:learning.*"])  # Read-only scope

        with pytest.raises(ValueError, match="[Ss]cope"):
            operations.learn(
                token=token,
                key="test_key",
                statement="Test statement",
            )

    def test_learn_upsert_updates_existing(self, operations):
        """Learn with same key updates existing."""
        token = make_token(["learn:write", "query:learning.*"])

        result1 = operations.learn(
            token=token,
            key="test_key",
            statement="Original",
        )

        result2 = operations.learn(
            token=token,
            key="test_key",
            statement="Updated",
            upsert=True,
        )

        assert result2["id"] == result1["id"]
        # previous contains the disclosure-filtered payload (summary level)
        assert result2.get("previous") is not None
        assert "test_key" in result2["previous"].get("summary", "")

    def test_learn_with_category(self, operations, storage):
        """Learn stores category metadata."""
        token = make_token(["learn:write"])

        result = operations.learn(
            token=token,
            key="pref_ide",
            statement="User prefers VS Code",
            category="preferences",
        )

        # Category is stored in the object, not returned at top level
        # Verify it's stored correctly
        stored = storage.get(result["id"])
        assert stored["payload"]["category"] == "preferences"
        assert "preferences" in stored["envelope"]["tags"]

    def test_learn_with_confidence(self, operations, storage):
        """Learn stores confidence score."""
        token = make_token(["learn:write"])

        result = operations.learn(
            token=token,
            key="work_hours",
            statement="User works 9am-5pm",
            confidence=0.8,
        )

        # Confidence is stored in the object, not returned at top level
        stored = storage.get(result["id"])
        assert stored["payload"]["confidence"] == 0.8
        assert stored["envelope"]["lineage"]["confidence"] == 0.8


class TestScopeValidation:
    def test_admin_scope_allows_all(self, operations, sample_events):
        """Admin scope allows all operations."""
        token = make_token(["pcp:admin"])

        # Query should work
        result = operations.query(token=token, object_types=["event"])
        assert result["count"] == 5

    def test_wildcard_scope(self, operations, sample_events):
        """Wildcard scope grants access to all disclosure levels."""
        token = make_token(["query:event.*"])

        # Should be able to query with any disclosure
        for level in ["summary", "detail", "raw"]:
            result = operations.query(
                token=token,
                object_types=["event"],
                disclosure=level,
            )
            assert result["count"] == 5

    def test_specific_disclosure_scope(self, operations, sample_events):
        """Specific disclosure scope limits access."""
        token = make_token(["query:event.summary"])

        # Summary should work
        result = operations.query(
            token=token,
            object_types=["event"],
            disclosure="summary",
        )
        assert result["count"] == 5

        # Detail should fail
        with pytest.raises(ValueError, match="[Ss]cope"):
            operations.query(
                token=token,
                object_types=["event"],
                disclosure="detail",
            )
