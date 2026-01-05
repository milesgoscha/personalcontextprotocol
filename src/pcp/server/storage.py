"""
PCP Storage layer.

Provides persistence for PCP objects (identity, events, learnings, reflections).
Default implementation uses local JSON files; can be extended for other backends.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from pcp.models.envelope import DisclosureLevel, ObjectType, PCPObject


@dataclass
class QueryResult:
    """Result of a storage query."""

    items: list[PCPObject]
    count: int
    next_cursor: str | None = None
    remaining_estimate: int | None = None


@dataclass
class Storage:
    """
    PCP object storage.

    Supports:
    - CRUD operations for all object types
    - Filtering by type, tags, time range
    - Cursor-based pagination
    - Progressive disclosure filtering
    """

    data_dir: Path
    _objects: dict[str, dict[str, Any]] = field(default_factory=dict)
    _identity: dict[str, Any] | None = None

    def __post_init__(self):
        self.data_dir = Path(self.data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._load()

    def _load(self) -> None:
        """Load objects from disk."""
        objects_file = self.data_dir / "objects.jsonl"
        if objects_file.exists():
            with open(objects_file) as f:
                for line in f:
                    if line.strip():
                        obj = json.loads(line)
                        obj_id = obj.get("envelope", {}).get("id", "")
                        if obj_id:
                            self._objects[obj_id] = obj

        identity_file = self.data_dir / "identity.json"
        if identity_file.exists():
            with open(identity_file) as f:
                self._identity = json.load(f)

    def _persist_object(self, obj: dict[str, Any]) -> None:
        """Append object to persistent store."""
        objects_file = self.data_dir / "objects.jsonl"
        with open(objects_file, "a") as f:
            f.write(json.dumps(obj) + "\n")

    def _persist_identity(self) -> None:
        """Persist identity to disk."""
        identity_file = self.data_dir / "identity.json"
        with open(identity_file, "w") as f:
            json.dump(self._identity, f, indent=2)

    # Identity operations

    def get_identity(self) -> dict[str, Any] | None:
        """Get the user's identity."""
        return self._identity

    def set_identity(self, identity: dict[str, Any]) -> dict[str, Any]:
        """Set or update the user's identity."""
        self._identity = identity
        self._persist_identity()
        return identity

    # Generic object operations

    def store(self, obj: dict[str, Any]) -> str:
        """Store an object and return its ID."""
        envelope = obj.get("envelope", {})

        # Generate ID if not present
        if not envelope.get("id"):
            obj_type = envelope.get("type", "obj")
            envelope["id"] = f"pcp://local/{obj_type}/{uuid4().hex[:12]}"
            obj["envelope"] = envelope

        # Set timestamps
        now = datetime.utcnow().isoformat()
        if not envelope.get("created_at"):
            envelope["created_at"] = now
        envelope["updated_at"] = now

        obj_id = envelope["id"]
        self._objects[obj_id] = obj
        self._persist_object(obj)

        return obj_id

    def get(self, obj_id: str) -> dict[str, Any] | None:
        """Get an object by ID."""
        return self._objects.get(obj_id)

    def query(
        self,
        object_types: list[ObjectType] | None = None,
        tags: list[str] | None = None,
        tags_include: list[str] | None = None,
        tags_exclude: list[str] | None = None,
        time_from: datetime | None = None,
        time_to: datetime | None = None,
        predicates: list[dict[str, Any]] | None = None,
        disclosure: DisclosureLevel = DisclosureLevel.SUMMARY,
        limit: int = 100,
        cursor: str | None = None,
        ids: list[str] | None = None,
    ) -> QueryResult:
        """
        Query objects with filtering and pagination.

        Args:
            object_types: Filter by object type
            tags: Filter by tags (ANY match - legacy, prefer tags_include)
            tags_include: Require ALL of these tags (AND logic)
            tags_exclude: Exclude items with ANY of these tags
            time_from: Filter by created_at >= time_from
            time_to: Filter by created_at <= time_to
            predicates: JSONPath-like predicates [{path, op, value}]
            disclosure: Disclosure level to return
            limit: Maximum items to return
            cursor: Pagination cursor
            ids: Specific IDs to fetch (overrides other filters)

        Returns:
            QueryResult with items and pagination info
        """
        # If specific IDs requested, fetch those directly
        if ids:
            items = []
            for obj_id in ids:
                obj = self._objects.get(obj_id)
                if obj:
                    items.append(self._apply_disclosure(obj, disclosure))
            return QueryResult(items=items, count=len(items))

        # Otherwise, filter all objects
        matching = list(self._filter_objects(
            object_types=object_types,
            tags=tags,
            tags_include=tags_include,
            tags_exclude=tags_exclude,
            time_from=time_from,
            time_to=time_to,
            predicates=predicates,
        ))

        # Sort by created_at descending
        matching.sort(
            key=lambda o: o.get("envelope", {}).get("created_at", ""),
            reverse=True,
        )

        # Apply pagination
        start_idx = 0
        if cursor:
            # Cursor is the index to start from
            try:
                start_idx = int(cursor)
            except ValueError:
                pass

        end_idx = start_idx + limit
        page = matching[start_idx:end_idx]

        # Apply disclosure level
        items = [self._apply_disclosure(obj, disclosure) for obj in page]

        # Build result
        next_cursor = None
        remaining = len(matching) - end_idx
        if remaining > 0:
            next_cursor = str(end_idx)

        return QueryResult(
            items=items,
            count=len(matching),
            next_cursor=next_cursor,
            remaining_estimate=max(0, remaining),
        )

    def _filter_objects(
        self,
        object_types: list[ObjectType] | None = None,
        tags: list[str] | None = None,
        tags_include: list[str] | None = None,
        tags_exclude: list[str] | None = None,
        time_from: datetime | None = None,
        time_to: datetime | None = None,
        predicates: list[dict[str, Any]] | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Filter objects by criteria."""
        type_values = [t.value for t in (object_types or [])]

        for obj in self._objects.values():
            envelope = obj.get("envelope", {})
            obj_tags = set(envelope.get("tags", []))

            # Type filter
            if type_values and envelope.get("type") not in type_values:
                continue

            # Tags filter (ANY match - legacy)
            if tags:
                if not any(t in obj_tags for t in tags):
                    continue

            # Tags include filter (ALL must match)
            if tags_include:
                if not all(t in obj_tags for t in tags_include):
                    continue

            # Tags exclude filter (NONE must match)
            if tags_exclude:
                if any(t in obj_tags for t in tags_exclude):
                    continue

            # Time filter
            created_at_str = envelope.get("created_at", "")
            if created_at_str:
                try:
                    created_at = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
                    if time_from and created_at < time_from:
                        continue
                    if time_to and created_at > time_to:
                        continue
                except ValueError:
                    pass

            # Predicate filters
            if predicates:
                if not self._matches_predicates(obj, predicates):
                    continue

            yield obj

    def _matches_predicates(
        self, obj: dict[str, Any], predicates: list[dict[str, Any]]
    ) -> bool:
        """Check if object matches all predicates."""
        for pred in predicates:
            path = pred.get("path", "")
            op = pred.get("op", "eq")
            value = pred.get("value")

            # Navigate path
            current = obj
            for part in path.split("."):
                if isinstance(current, dict):
                    current = current.get(part)
                else:
                    current = None
                    break

            # Apply operator
            if op == "eq" and current != value:
                return False
            elif op == "ne" and current == value:
                return False
            elif op == "contains" and (not isinstance(current, str) or value not in current):
                return False
            elif op == "matches":
                import re
                if not isinstance(current, str) or not re.search(value, current):
                    return False

        return True

    def _apply_disclosure(
        self, obj: dict[str, Any], level: DisclosureLevel
    ) -> dict[str, Any]:
        """Apply disclosure level to object."""
        envelope = obj.get("envelope", {})
        payload = obj.get("payload", {})

        # Check what levels are available
        available = envelope.get("disclosure", {}).get(
            "available_levels", ["summary", "detail"]
        )

        # Determine what to return
        if level == DisclosureLevel.SUMMARY:
            # Only return summary fields
            filtered_payload = {"summary": payload.get("summary", "")}
        elif level == DisclosureLevel.DETAIL:
            # Return summary + detail, exclude raw
            filtered_payload = {
                k: v for k, v in payload.items()
                if k != "raw_ref"
            }
        else:  # RAW
            filtered_payload = payload

        return {
            "envelope": envelope,
            "payload": filtered_payload,
            "disclosure_level": level.value,
            "detail_available": "detail" in available,
            "raw_available": "raw" in available,
        }

    def update(self, obj_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
        """Update an existing object."""
        obj = self._objects.get(obj_id)
        if not obj:
            return None

        # Deep merge updates
        self._deep_merge(obj, updates)

        # Update timestamp
        obj["envelope"]["updated_at"] = datetime.utcnow().isoformat()

        # Re-persist (note: this creates a new line, not ideal for production)
        self._persist_object(obj)

        return obj

    def _deep_merge(self, target: dict, source: dict) -> None:
        """Deep merge source into target."""
        for key, value in source.items():
            if key in target and isinstance(target[key], dict) and isinstance(value, dict):
                self._deep_merge(target[key], value)
            else:
                target[key] = value

    def delete(self, obj_id: str) -> bool:
        """Delete an object by ID."""
        if obj_id in self._objects:
            del self._objects[obj_id]
            # Note: doesn't remove from JSONL file (append-only)
            return True
        return False

    def count(self, object_types: list[ObjectType] | None = None) -> int:
        """Count objects, optionally filtered by type."""
        if not object_types:
            return len(self._objects)

        type_values = [t.value for t in object_types]
        return sum(
            1 for obj in self._objects.values()
            if obj.get("envelope", {}).get("type") in type_values
        )
