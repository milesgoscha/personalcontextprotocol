"""
PCP Scope definitions and validation.

Scopes follow the pattern: operation:object_type[.disclosure_level]

Examples:
- query:identity          - can query identity
- query:event.summary     - can query events at summary level only
- query:event.detail      - can query events up to detail level
- query:event.*           - can query events at any disclosure level
- query:learning.*        - can query learnings at any level
- observe:event           - can observe (write) events
- learn:write             - can create/update learnings
- reflect:write           - can create reflections
- identity:read           - can read identity
- identity:write          - can update identity
- pcp:admin               - full admin access (use sparingly)
"""

from dataclasses import dataclass
from enum import Enum
from typing import Self

from pcp.models.envelope import DisclosureLevel, ObjectType


class Operation(str, Enum):
    """PCP operations that can be scoped."""

    QUERY = "query"
    OBSERVE = "observe"
    LEARN = "learn"
    REFLECT = "reflect"
    DESCRIBE = "describe"  # Always allowed, but can be audited
    SUBSCRIBE = "subscribe"
    IDENTITY = "identity"  # Read/write identity
    ADMIN = "admin"


# Mapping operations to the capability taxonomy defined in docs/SPEC.md.
# This is informative metadata so the reference node can report which parts
# of the spec it satisfies.
OPERATION_CAPABILITIES: dict[Operation, str] = {
    Operation.QUERY: "READ",
    Operation.OBSERVE: "APPEND",
    Operation.LEARN: "MUTATE",
    Operation.REFLECT: "DERIVE",
    Operation.DESCRIBE: "SCOPE_QUERY",
    Operation.SUBSCRIBE: "SCOPE_QUERY",
    Operation.IDENTITY: "READ",
    Operation.ADMIN: "MUTATE",
}


@dataclass
class Scope:
    """A single scope permission."""

    operation: Operation
    object_type: ObjectType | None = None
    disclosure_level: DisclosureLevel | None = None  # None means wildcard (*)
    is_write: bool = False  # For identity:read vs identity:write distinction

    def __str__(self) -> str:
        parts = [self.operation.value]
        if self.object_type:
            parts.append(self.object_type.value)
            if self.disclosure_level:
                parts.append(self.disclosure_level.value)
            elif self.disclosure_level is None and self.operation == Operation.QUERY:
                # Explicitly show wildcard for query operations
                pass  # Will be shown as query:event (implies summary only) or query:event.*
        return ":".join(parts)

    def allows_disclosure(self, level: DisclosureLevel) -> bool:
        """Check if this scope allows the given disclosure level."""
        if self.disclosure_level is None:
            # Wildcard - allows all levels
            return True

        # Disclosure levels are hierarchical: summary < detail < raw
        level_order = {
            DisclosureLevel.SUMMARY: 0,
            DisclosureLevel.DETAIL: 1,
            DisclosureLevel.RAW: 2,
        }
        return level_order[level] <= level_order[self.disclosure_level]

    def matches(
        self,
        operation: Operation,
        object_type: ObjectType | None = None,
        requires_write: bool = False,
    ) -> bool:
        """Check if this scope matches the given operation and object type."""
        if self.operation == Operation.ADMIN:
            return True  # Admin scope matches everything

        if self.operation != operation:
            # Special case: query:identity also grants identity:read
            if (
                operation == Operation.IDENTITY
                and self.operation == Operation.QUERY
                and self.object_type == ObjectType.IDENTITY
                and not requires_write
            ):
                return True
            return False

        # For identity operation, check read/write distinction
        if operation == Operation.IDENTITY:
            if requires_write and not self.is_write:
                return False

        if self.object_type is None:
            return True  # Wildcard object type

        return self.object_type == object_type


def parse_scope(scope_str: str) -> Scope:
    """
    Parse a scope string into a Scope object.

    Examples:
        "query:event.summary" -> Scope(QUERY, EVENT, SUMMARY)
        "query:event.*" -> Scope(QUERY, EVENT, None)
        "query:event" -> Scope(QUERY, EVENT, SUMMARY)  # Default to summary
        "observe:event" -> Scope(OBSERVE, EVENT, None)
        "learn:write" -> Scope(LEARN, LEARNING, None)  # write means write access
        "reflect:write" -> Scope(REFLECT, REFLECTION, None)
        "pcp:admin" -> Scope(ADMIN, None, None)
    """
    parts = scope_str.split(":")

    if len(parts) < 1:
        raise ValueError(f"Invalid scope format: {scope_str}")

    # Handle pcp:admin special case
    if parts[0] == "pcp" and len(parts) > 1 and parts[1] == "admin":
        return Scope(operation=Operation.ADMIN)

    operation = Operation(parts[0])

    if len(parts) < 2:
        return Scope(operation=operation)

    # Handle "write" and "read" as special markers
    if parts[1] in ("write", "read"):
        # Map operation to its default object type
        op_to_type = {
            Operation.LEARN: ObjectType.LEARNING,
            Operation.REFLECT: ObjectType.REFLECTION,
            Operation.OBSERVE: ObjectType.EVENT,
            Operation.IDENTITY: ObjectType.IDENTITY,
        }
        return Scope(
            operation=operation,
            object_type=op_to_type.get(operation),
            disclosure_level=None,
            is_write=(parts[1] == "write"),
        )

    # Parse object_type and optional disclosure level
    type_parts = parts[1].split(".")
    object_type = ObjectType(type_parts[0])

    disclosure_level = None
    if len(type_parts) > 1:
        if type_parts[1] == "*":
            disclosure_level = None  # Wildcard
        else:
            disclosure_level = DisclosureLevel(type_parts[1])
    elif operation == Operation.QUERY:
        # Default to summary for query operations without explicit level
        disclosure_level = DisclosureLevel.SUMMARY

    return Scope(
        operation=operation,
        object_type=object_type,
        disclosure_level=disclosure_level,
    )


def describe_scope(scope_str: str) -> dict[str, str | None]:
    """
    Return a capability-aware description of a scope string.

    This is used in API responses to anchor scopes to the PCP spec's capability
    and scope clauses, making it clear which semantics apply.
    """
    scope = parse_scope(scope_str)
    capability = OPERATION_CAPABILITIES.get(scope.operation)
    return {
        "scope": scope_str,
        "operation": scope.operation.value,
        "capability": capability,
        "object_type": scope.object_type.value if scope.object_type else "*",
        "disclosure": scope.disclosure_level.value if scope.disclosure_level else "*",
        "spec_ref": {
            "capabilities": "PCP §5",
            "scope": "PCP §6",
        },
    }


class ScopeSet:
    """A set of scopes for a token."""

    def __init__(self, scopes: list[Scope] | None = None):
        self.scopes = scopes or []

    @classmethod
    def from_strings(cls, scope_strings: list[str]) -> Self:
        """Create a ScopeSet from a list of scope strings."""
        return cls([parse_scope(s) for s in scope_strings])

    def allows(
        self,
        operation: Operation,
        object_type: ObjectType | None = None,
        disclosure_level: DisclosureLevel = DisclosureLevel.SUMMARY,
        requires_write: bool = False,
    ) -> bool:
        """Check if this scope set allows the given operation."""
        for scope in self.scopes:
            if scope.matches(operation, object_type, requires_write):
                if operation == Operation.QUERY:
                    if scope.allows_disclosure(disclosure_level):
                        return True
                else:
                    return True
        return False

    def max_disclosure_for(self, object_type: ObjectType) -> DisclosureLevel | None:
        """Get the maximum allowed disclosure level for an object type."""
        max_level = None
        level_order = {
            DisclosureLevel.SUMMARY: 0,
            DisclosureLevel.DETAIL: 1,
            DisclosureLevel.RAW: 2,
        }

        for scope in self.scopes:
            if scope.operation == Operation.ADMIN:
                return DisclosureLevel.RAW  # Admin can see everything

            if scope.operation == Operation.QUERY and scope.matches(
                Operation.QUERY, object_type
            ):
                if scope.disclosure_level is None:
                    return DisclosureLevel.RAW  # Wildcard means full access

                if max_level is None or level_order[scope.disclosure_level] > level_order[
                    max_level
                ]:
                    max_level = scope.disclosure_level

        return max_level

    def __iter__(self):
        return iter(self.scopes)

    def __len__(self):
        return len(self.scopes)


def validate_scope(
    scope_set: ScopeSet,
    operation: Operation,
    object_type: ObjectType | None = None,
    disclosure_level: DisclosureLevel = DisclosureLevel.SUMMARY,
    requires_write: bool = False,
) -> bool:
    """
    Validate that a scope set allows the requested operation.

    Raises ValueError if not allowed.
    """
    if not scope_set.allows(operation, object_type, disclosure_level, requires_write):
        write_str = ":write" if requires_write else ""
        raise ValueError(
            f"Scope does not allow {operation.value}:{object_type.value if object_type else '*'}"
            f"{write_str}"
        )
    return True
