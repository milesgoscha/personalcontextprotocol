"""Tests for storage CRUD operations including persistence."""

import json
import pytest
from pathlib import Path

from pcp.server.storage import Storage
from pcp.models.envelope import ObjectType, DisclosureLevel


class TestStorageBasicOperations:
    def test_store_and_get(self, storage):
        """Basic store and retrieve."""
        obj_id = storage.store({
            "envelope": {"type": "event"},
            "payload": {"summary": "Test event"},
        })

        retrieved = storage.get(obj_id)
        assert retrieved is not None
        assert retrieved["payload"]["summary"] == "Test event"

    def test_store_generates_id(self, storage):
        """Store generates ID if not provided."""
        obj_id = storage.store({
            "envelope": {"type": "event"},
            "payload": {"summary": "Test"},
        })

        assert obj_id.startswith("pcp://local/event/")

    def test_store_sets_timestamps(self, storage):
        """Store sets created_at and updated_at."""
        obj_id = storage.store({
            "envelope": {"type": "event"},
            "payload": {"summary": "Test"},
        })

        obj = storage.get(obj_id)
        assert "created_at" in obj["envelope"]
        assert "updated_at" in obj["envelope"]

    def test_get_nonexistent_returns_none(self, storage):
        """Getting a nonexistent object returns None."""
        result = storage.get("pcp://local/event/nonexistent")
        assert result is None


class TestStorageDelete:
    def test_delete_removes_from_memory(self, storage):
        """Delete removes object from memory."""
        obj_id = storage.store({
            "envelope": {"type": "event"},
            "payload": {"summary": "Test"},
        })

        result = storage.delete(obj_id)

        assert result is True
        assert storage.get(obj_id) is None

    def test_delete_nonexistent_returns_false(self, storage):
        """Delete of nonexistent object returns False."""
        result = storage.delete("pcp://local/event/nonexistent")
        assert result is False

    def test_delete_persists_across_restart(self, temp_data_dir):
        """Deleted objects stay deleted after restart."""
        # Create and delete
        storage1 = Storage(data_dir=temp_data_dir)
        obj_id = storage1.store({
            "envelope": {"type": "event"},
            "payload": {"summary": "Test"},
        })
        storage1.delete(obj_id)

        # Recreate storage (simulates restart)
        storage2 = Storage(data_dir=temp_data_dir)

        # Should still be deleted
        assert storage2.get(obj_id) is None

    def test_tombstone_written_to_file(self, storage, temp_data_dir):
        """Delete writes tombstone to JSONL file."""
        obj_id = storage.store({
            "envelope": {"type": "event"},
            "payload": {"summary": "Test"},
        })
        storage.delete(obj_id)

        # Check file contents
        objects_file = temp_data_dir / "objects.jsonl"
        lines = objects_file.read_text().strip().split("\n")

        # Last line should be tombstone
        tombstone = json.loads(lines[-1])
        assert tombstone["envelope"]["id"] == obj_id
        assert tombstone["envelope"]["__tombstone"] is True
        assert "deleted_at" in tombstone["envelope"]


class TestStorageUpdate:
    def test_update_modifies_object(self, storage):
        """Update modifies object in memory."""
        obj_id = storage.store({
            "envelope": {"type": "event"},
            "payload": {"summary": "Original"},
        })

        storage.update(obj_id, {"payload": {"summary": "Updated"}})

        obj = storage.get(obj_id)
        assert obj["payload"]["summary"] == "Updated"

    def test_update_persists_across_restart(self, temp_data_dir):
        """Updated objects retain changes after restart."""
        storage1 = Storage(data_dir=temp_data_dir)
        obj_id = storage1.store({
            "envelope": {"type": "event"},
            "payload": {"summary": "Original"},
        })
        storage1.update(obj_id, {"payload": {"summary": "Updated"}})

        storage2 = Storage(data_dir=temp_data_dir)
        obj = storage2.get(obj_id)
        assert obj["payload"]["summary"] == "Updated"

    def test_update_nonexistent_returns_none(self, storage):
        """Updating nonexistent object returns None."""
        result = storage.update("pcp://local/event/nonexistent", {"payload": {}})
        assert result is None


class TestStorageQuery:
    def test_query_by_type(self, storage, sample_events):
        """Query filters by object type."""
        # Add a learning
        storage.store({
            "envelope": {"type": "learning"},
            "payload": {"summary": "A learning"},
        })

        result = storage.query(object_types=[ObjectType.EVENT])

        assert result.count == 5
        for item in result.items:
            assert item["envelope"]["type"] == "event"

    def test_query_pagination(self, storage, sample_events):
        """Query supports pagination."""
        result1 = storage.query(
            object_types=[ObjectType.EVENT],
            limit=2,
        )

        assert len(result1.items) == 2
        assert result1.next_cursor is not None

        result2 = storage.query(
            object_types=[ObjectType.EVENT],
            limit=2,
            cursor=result1.next_cursor,
        )

        assert len(result2.items) == 2
        # Items should be different
        ids1 = {i["envelope"]["id"] for i in result1.items}
        ids2 = {i["envelope"]["id"] for i in result2.items}
        assert ids1.isdisjoint(ids2)

    def test_query_by_ids(self, storage, sample_events):
        """Query can fetch specific IDs."""
        # Get first two IDs
        target_ids = sample_events[:2]

        result = storage.query(ids=target_ids)

        assert result.count == 2
        returned_ids = {i["envelope"]["id"] for i in result.items}
        assert returned_ids == set(target_ids)


class TestStorageCompact:
    def test_compact_removes_duplicates(self, temp_data_dir):
        """Compact removes duplicate entries from JSONL."""
        storage = Storage(data_dir=temp_data_dir)

        # Create and update (creates duplicate in file)
        obj_id = storage.store({
            "envelope": {"type": "event"},
            "payload": {"summary": "Original"},
        })
        storage.update(obj_id, {"payload": {"summary": "Updated"}})

        # File should have 2 lines
        objects_file = temp_data_dir / "objects.jsonl"
        with open(objects_file) as f:
            lines_before = sum(1 for line in f if line.strip())
        assert lines_before == 2

        # Compact
        removed = storage.compact()

        # Should have removed 1 duplicate
        assert removed == 1

        # File should have 1 line
        with open(objects_file) as f:
            lines_after = sum(1 for line in f if line.strip())
        assert lines_after == 1

        # Object should still be accessible
        obj = storage.get(obj_id)
        assert obj["payload"]["summary"] == "Updated"

    def test_compact_removes_tombstones(self, temp_data_dir):
        """Compact removes tombstone entries from JSONL."""
        storage = Storage(data_dir=temp_data_dir)

        # Create and delete
        obj_id = storage.store({
            "envelope": {"type": "event"},
            "payload": {"summary": "Test"},
        })
        storage.delete(obj_id)

        # File should have 2 lines (object + tombstone)
        objects_file = temp_data_dir / "objects.jsonl"
        with open(objects_file) as f:
            lines_before = sum(1 for line in f if line.strip())
        assert lines_before == 2

        # Compact
        removed = storage.compact()

        # Should have removed 2 entries (object + tombstone)
        assert removed == 2

        # File should be empty
        with open(objects_file) as f:
            lines_after = sum(1 for line in f if line.strip())
        assert lines_after == 0


class TestStorageCount:
    def test_count_all(self, storage, sample_events):
        """Count returns total object count."""
        count = storage.count()
        assert count == 5

    def test_count_by_type(self, storage, sample_events):
        """Count can filter by type."""
        # Add a learning
        storage.store({
            "envelope": {"type": "learning"},
            "payload": {"summary": "A learning"},
        })

        event_count = storage.count(object_types=[ObjectType.EVENT])
        learning_count = storage.count(object_types=[ObjectType.LEARNING])

        assert event_count == 5
        assert learning_count == 1
