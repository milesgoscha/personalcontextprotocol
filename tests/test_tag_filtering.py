"""Tests for tag-based filtering in PCP storage."""

import pytest
import tempfile
from pathlib import Path

from pcp.server.storage import Storage
from pcp.models.envelope import ObjectType, DisclosureLevel


@pytest.fixture
def storage():
    """Create a temporary storage instance."""
    with tempfile.TemporaryDirectory() as tmpdir:
        s = Storage(data_dir=Path(tmpdir))

        # Add some test events with various tags
        s.store({
            "envelope": {"type": "event", "tags": ["work", "browser", "focus"]},
            "payload": {"summary": "Work browsing session", "event_kind": "activity"}
        })
        s.store({
            "envelope": {"type": "event", "tags": ["personal", "browser"]},
            "payload": {"summary": "Personal browsing", "event_kind": "activity"}
        })
        s.store({
            "envelope": {"type": "event", "tags": ["work", "code"]},
            "payload": {"summary": "Coding session", "event_kind": "activity"}
        })
        s.store({
            "envelope": {"type": "event", "tags": ["personal", "social"]},
            "payload": {"summary": "Social media", "event_kind": "activity"}
        })
        s.store({
            "envelope": {"type": "event", "tags": ["work", "meeting"]},
            "payload": {"summary": "Team meeting", "event_kind": "activity"}
        })

        yield s


class TestTagIncludeFilter:
    def test_single_tag_include(self, storage):
        """Items must have all tags in tags_include."""
        result = storage.query(
            object_types=[ObjectType.EVENT],
            tags_include=["work"],
        )
        # Should get: work+browser+focus, work+code, work+meeting
        assert result.count == 3
        for item in result.items:
            assert "work" in item["envelope"]["tags"]

    def test_multiple_tag_include_all_required(self, storage):
        """All tags in tags_include must be present."""
        result = storage.query(
            object_types=[ObjectType.EVENT],
            tags_include=["work", "browser"],
        )
        # Should only get: work+browser+focus
        assert result.count == 1
        assert "browser" in result.items[0]["envelope"]["tags"]
        assert "work" in result.items[0]["envelope"]["tags"]

    def test_no_match_when_missing_tag(self, storage):
        """Return empty if no items have all required tags."""
        result = storage.query(
            object_types=[ObjectType.EVENT],
            tags_include=["work", "social"],  # No item has both
        )
        assert result.count == 0


class TestTagExcludeFilter:
    def test_single_tag_exclude(self, storage):
        """Items with any excluded tag are filtered out."""
        result = storage.query(
            object_types=[ObjectType.EVENT],
            tags_exclude=["personal"],
        )
        # Should get: work+browser+focus, work+code, work+meeting
        assert result.count == 3
        for item in result.items:
            assert "personal" not in item["envelope"]["tags"]

    def test_multiple_tag_exclude(self, storage):
        """Items with any of the excluded tags are filtered out."""
        result = storage.query(
            object_types=[ObjectType.EVENT],
            tags_exclude=["browser", "social"],
        )
        # Should get: work+code, work+meeting
        assert result.count == 2
        for item in result.items:
            assert "browser" not in item["envelope"]["tags"]
            assert "social" not in item["envelope"]["tags"]


class TestCombinedTagFilters:
    def test_include_and_exclude_together(self, storage):
        """Can combine include and exclude filters."""
        result = storage.query(
            object_types=[ObjectType.EVENT],
            tags_include=["work"],
            tags_exclude=["meeting"],
        )
        # Should get: work+browser+focus, work+code
        assert result.count == 2
        for item in result.items:
            assert "work" in item["envelope"]["tags"]
            assert "meeting" not in item["envelope"]["tags"]

    def test_legacy_tags_filter_still_works(self, storage):
        """Legacy tags parameter (ANY match) still works."""
        result = storage.query(
            object_types=[ObjectType.EVENT],
            tags=["browser", "code"],  # ANY of these
        )
        # Should get: work+browser+focus, personal+browser, work+code
        assert result.count == 3

    def test_all_filters_combined(self, storage):
        """Can combine legacy tags, include, and exclude."""
        result = storage.query(
            object_types=[ObjectType.EVENT],
            tags=["browser"],  # Must have browser (ANY match with one item = exact)
            tags_include=["work"],  # Must have work
            tags_exclude=["focus"],  # Exclude focus
        )
        # Only work+browser+focus has both browser AND work, but it has focus
        # So this should return 0
        assert result.count == 0
