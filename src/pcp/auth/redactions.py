"""
PCP Redaction Policy Engine.

Applies field-level redactions and disclosure ceilings based on trust tiers.
"""

import copy
import re
from dataclasses import dataclass
from typing import Any

from .grants import TIER_DEFAULTS, TrustTier


# Disclosure level ordering (lower index = less data)
DISCLOSURE_LEVELS = ["summary", "detail", "raw"]


@dataclass
class RedactionPolicy:
    """Policy for a specific trust tier."""

    trust_tier: TrustTier
    disclosure_max: str
    redact_fields: list[str]

    @classmethod
    def for_tier(cls, tier: TrustTier) -> "RedactionPolicy":
        """Get the redaction policy for a trust tier."""
        defaults = TIER_DEFAULTS[tier]
        return cls(
            trust_tier=tier,
            disclosure_max=defaults["disclosure_max"],
            redact_fields=defaults["redactions"],
        )


def _parse_field_path(path: str) -> list[str]:
    """Parse a dot-notation path into segments."""
    return path.split(".")


def _matches_pattern(path_segments: list[str], pattern_segments: list[str]) -> bool:
    """
    Check if a path matches a pattern.

    Supports:
    - Exact match: "payload.detail.url"
    - Wildcard: "payload.detail.*" matches all fields under detail
    - Deep wildcard: "envelope.lineage.*" matches all nested fields
    """
    if len(pattern_segments) == 0:
        return len(path_segments) == 0

    # If pattern ends with *, match prefix
    if pattern_segments[-1] == "*":
        prefix = pattern_segments[:-1]
        if len(path_segments) < len(prefix):
            return False
        return path_segments[: len(prefix)] == prefix

    # Exact match
    return path_segments == pattern_segments


def _get_all_paths(obj: dict, prefix: str = "") -> list[str]:
    """Get all dot-notation paths in a nested dict."""
    paths = []
    for key, value in obj.items():
        path = f"{prefix}.{key}" if prefix else key
        paths.append(path)
        if isinstance(value, dict):
            paths.extend(_get_all_paths(value, path))
    return paths


def _remove_field(obj: dict, path_segments: list[str]) -> bool:
    """
    Remove a field from a nested dict by path.

    Returns True if the field was found and removed.
    """
    if not path_segments:
        return False

    current = obj
    for i, segment in enumerate(path_segments[:-1]):
        if not isinstance(current, dict) or segment not in current:
            return False
        current = current[segment]

    final_key = path_segments[-1]
    if isinstance(current, dict) and final_key in current:
        del current[final_key]
        return True
    return False


def _get_field(obj: dict, path_segments: list[str]) -> Any:
    """Get a field value from a nested dict by path."""
    current = obj
    for segment in path_segments:
        if not isinstance(current, dict) or segment not in current:
            return None
        current = current[segment]
    return current


def apply_redactions(
    item: dict[str, Any],
    trust_tier: TrustTier,
    requested_disclosure: str = "summary",
) -> tuple[dict[str, Any], list[str]]:
    """
    Apply redaction policy to an item.

    Args:
        item: The PCP object (envelope + payload)
        trust_tier: The trust tier of the requesting agent
        requested_disclosure: The disclosure level requested

    Returns:
        Tuple of (redacted_item, list_of_redacted_fields)
    """
    policy = RedactionPolicy.for_tier(trust_tier)
    result = copy.deepcopy(item)
    redacted_fields = []

    # Step 1: Enforce disclosure ceiling
    max_level_idx = DISCLOSURE_LEVELS.index(policy.disclosure_max)
    requested_idx = DISCLOSURE_LEVELS.index(requested_disclosure)
    effective_disclosure = DISCLOSURE_LEVELS[min(max_level_idx, requested_idx)]

    # If disclosure is capped, remove higher-level data
    if effective_disclosure != "raw":
        # Remove raw_ref if not at raw level
        if "payload" in result and "raw_ref" in result["payload"]:
            del result["payload"]["raw_ref"]
            redacted_fields.append("payload.raw_ref")

    if effective_disclosure == "summary":
        # Remove detail section entirely
        if "payload" in result and "detail" in result["payload"]:
            del result["payload"]["detail"]
            redacted_fields.append("payload.detail")

    # Step 2: Apply field-specific redactions
    all_paths = _get_all_paths(result)

    for pattern in policy.redact_fields:
        pattern_segments = _parse_field_path(pattern)

        for path in all_paths:
            path_segments = _parse_field_path(path)
            if _matches_pattern(path_segments, pattern_segments):
                if _remove_field(result, path_segments):
                    redacted_fields.append(path)

    # Step 3: Add redaction metadata
    if redacted_fields:
        if "envelope" not in result:
            result["envelope"] = {}
        result["envelope"]["redacted_fields"] = redacted_fields
        result["envelope"]["trust_tier"] = trust_tier.value
        result["envelope"]["effective_disclosure"] = effective_disclosure

    return result, redacted_fields


def apply_redactions_to_batch(
    items: list[dict[str, Any]],
    trust_tier: TrustTier,
    requested_disclosure: str = "summary",
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """
    Apply redaction policy to a batch of items.

    Args:
        items: List of PCP objects
        trust_tier: The trust tier of the requesting agent
        requested_disclosure: The disclosure level requested

    Returns:
        Tuple of (redacted_items, redaction_stats)
    """
    redacted_items = []
    stats: dict[str, int] = {"total_items": len(items), "redacted_items": 0}
    field_counts: dict[str, int] = {}

    for item in items:
        redacted_item, redacted_fields = apply_redactions(
            item, trust_tier, requested_disclosure
        )
        redacted_items.append(redacted_item)

        if redacted_fields:
            stats["redacted_items"] += 1
            for field in redacted_fields:
                field_counts[field] = field_counts.get(field, 0) + 1

    stats["field_redaction_counts"] = field_counts
    return redacted_items, stats


def get_effective_disclosure(
    trust_tier: TrustTier,
    requested_disclosure: str,
) -> str:
    """
    Get the effective disclosure level after applying tier ceiling.

    Args:
        trust_tier: The trust tier of the requesting agent
        requested_disclosure: The disclosure level requested

    Returns:
        The effective disclosure level (may be lower than requested)
    """
    policy = RedactionPolicy.for_tier(trust_tier)
    max_level_idx = DISCLOSURE_LEVELS.index(policy.disclosure_max)
    requested_idx = DISCLOSURE_LEVELS.index(requested_disclosure)
    return DISCLOSURE_LEVELS[min(max_level_idx, requested_idx)]
