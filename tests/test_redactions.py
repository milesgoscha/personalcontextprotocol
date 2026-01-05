"""Tests for the PCP redaction policy engine."""

import pytest

from pcp.auth.grants import TrustTier
from pcp.auth.redactions import (
    DISCLOSURE_LEVELS,
    RedactionPolicy,
    apply_redactions,
    apply_redactions_to_batch,
    get_effective_disclosure,
)


def make_event(with_detail=True, with_raw=True):
    """Create a test event object."""
    event = {
        "envelope": {
            "id": "pcp://test/evt/123",
            "type": "event",
            "tags": ["activity", "browser"],
            "lineage": {
                "sources": ["collector:test"],
                "confidence": 0.95,
            },
        },
        "payload": {
            "event_kind": "application.navigation",
            "timestamp": "2026-01-04T12:00:00Z",
            "summary": "Visited example.com",
        },
    }
    if with_detail:
        event["payload"]["detail"] = {
            "application": "Arc",
            "window_title": "Example Page",
            "url": "https://example.com/private/page",
        }
    if with_raw:
        event["payload"]["raw_ref"] = {
            "uri": "pcp://test/blob/raw-123",
            "encoding": "binary",
        }
    return event


class TestRedactionPolicy:
    def test_local_tier_no_redactions(self):
        policy = RedactionPolicy.for_tier(TrustTier.LOCAL)
        assert policy.disclosure_max == "raw"
        assert policy.redact_fields == []

    def test_first_party_tier_redacts_urls(self):
        policy = RedactionPolicy.for_tier(TrustTier.FIRST_PARTY_REMOTE)
        assert policy.disclosure_max == "detail"
        assert "payload.detail.url" in policy.redact_fields

    def test_third_party_tier_redacts_detail_and_lineage(self):
        policy = RedactionPolicy.for_tier(TrustTier.THIRD_PARTY)
        assert policy.disclosure_max == "summary"
        assert "payload.detail.*" in policy.redact_fields
        assert "envelope.lineage.*" in policy.redact_fields


class TestEffectiveDisclosure:
    def test_local_tier_allows_raw(self):
        assert get_effective_disclosure(TrustTier.LOCAL, "raw") == "raw"
        assert get_effective_disclosure(TrustTier.LOCAL, "detail") == "detail"
        assert get_effective_disclosure(TrustTier.LOCAL, "summary") == "summary"

    def test_first_party_caps_at_detail(self):
        assert get_effective_disclosure(TrustTier.FIRST_PARTY_REMOTE, "raw") == "detail"
        assert get_effective_disclosure(TrustTier.FIRST_PARTY_REMOTE, "detail") == "detail"
        assert get_effective_disclosure(TrustTier.FIRST_PARTY_REMOTE, "summary") == "summary"

    def test_third_party_caps_at_summary(self):
        assert get_effective_disclosure(TrustTier.THIRD_PARTY, "raw") == "summary"
        assert get_effective_disclosure(TrustTier.THIRD_PARTY, "detail") == "summary"
        assert get_effective_disclosure(TrustTier.THIRD_PARTY, "summary") == "summary"


class TestApplyRedactions:
    def test_local_tier_no_redaction(self):
        event = make_event()
        redacted, fields = apply_redactions(event, TrustTier.LOCAL, "raw")

        # Should have all data intact
        assert "detail" in redacted["payload"]
        assert "raw_ref" in redacted["payload"]
        assert "url" in redacted["payload"]["detail"]
        assert fields == []

    def test_first_party_removes_url(self):
        event = make_event()
        redacted, fields = apply_redactions(event, TrustTier.FIRST_PARTY_REMOTE, "detail")

        # Detail should be present but URL should be removed
        assert "detail" in redacted["payload"]
        assert "url" not in redacted["payload"]["detail"]
        assert "application" in redacted["payload"]["detail"]
        assert "payload.detail.url" in fields

    def test_first_party_removes_raw_ref(self):
        event = make_event()
        redacted, fields = apply_redactions(event, TrustTier.FIRST_PARTY_REMOTE, "detail")

        # raw_ref should be removed (disclosure ceiling is detail)
        assert "raw_ref" not in redacted["payload"]
        assert "payload.raw_ref" in fields

    def test_third_party_removes_all_detail(self):
        event = make_event()
        redacted, fields = apply_redactions(event, TrustTier.THIRD_PARTY, "summary")

        # Detail and raw_ref should be removed
        assert "detail" not in redacted["payload"]
        assert "raw_ref" not in redacted["payload"]
        # Summary should remain
        assert "summary" in redacted["payload"]
        assert redacted["payload"]["summary"] == "Visited example.com"

    def test_third_party_removes_lineage(self):
        event = make_event()
        redacted, fields = apply_redactions(event, TrustTier.THIRD_PARTY, "summary")

        # Lineage should be removed
        assert "lineage" not in redacted.get("envelope", {})

    def test_adds_redaction_metadata(self):
        event = make_event()
        redacted, fields = apply_redactions(event, TrustTier.THIRD_PARTY, "summary")

        # Should have redaction metadata
        assert "redacted_fields" in redacted["envelope"]
        assert "trust_tier" in redacted["envelope"]
        assert redacted["envelope"]["trust_tier"] == "third_party"
        assert redacted["envelope"]["effective_disclosure"] == "summary"

    def test_no_metadata_when_no_redactions(self):
        event = make_event(with_detail=False, with_raw=False)
        redacted, fields = apply_redactions(event, TrustTier.LOCAL, "summary")

        # No redaction metadata when nothing was redacted
        assert "redacted_fields" not in redacted.get("envelope", {})


class TestBatchRedactions:
    def test_batch_applies_to_all(self):
        events = [make_event() for _ in range(5)]
        redacted, stats = apply_redactions_to_batch(events, TrustTier.THIRD_PARTY, "summary")

        assert len(redacted) == 5
        assert stats["total_items"] == 5
        assert stats["redacted_items"] == 5

        # All items should have detail removed
        for item in redacted:
            assert "detail" not in item["payload"]

    def test_batch_counts_field_redactions(self):
        events = [make_event() for _ in range(3)]
        _, stats = apply_redactions_to_batch(events, TrustTier.FIRST_PARTY_REMOTE, "detail")

        # Should track field-level counts
        field_counts = stats["field_redaction_counts"]
        assert field_counts.get("payload.detail.url") == 3
        assert field_counts.get("payload.raw_ref") == 3
