"""Tests for the grant request/approval lifecycle."""

import pytest
from pathlib import Path

from pcp.auth.grants import GrantStore, GrantStatus, TrustTier


class TestGrantCreation:
    def test_create_third_party_grant_is_pending(self, grant_store):
        """Third-party grants start as pending."""
        grant, claim_secret = grant_store.create(
            client_id="test-client",
            client_name="Test App",
            scopes_requested=["query:event.summary"],
            reason="Testing",
            trust_tier=TrustTier.THIRD_PARTY,
        )

        assert grant.status == GrantStatus.PENDING
        assert grant.scopes_approved is None

    def test_grant_dict_includes_scope_descriptors(self, grant_store):
        """Grant serialization exposes capability semantics."""
        grant, _ = grant_store.create(
            client_id="test-client",
            client_name="Test App",
            scopes_requested=["query:event.summary"],
            reason="Testing",
            trust_tier=TrustTier.THIRD_PARTY,
        )

        data = grant.to_dict()
        requested = data["scope_descriptors"]["requested"]
        assert requested[0]["capability"] == "READ"
        assert requested[0]["spec_ref"]["capabilities"] == "PCP §5"
        assert data["spec_references"]["scope"] == "PCP §6"

    def test_create_local_grant_auto_approves(self, grant_store):
        """Local grants are auto-approved."""
        grant, claim_secret = grant_store.create(
            client_id="local-agent",
            client_name="Local Agent",
            scopes_requested=["query:event.*"],
            reason="Local access",
            trust_tier=TrustTier.LOCAL,
        )

        assert grant.status == GrantStatus.APPROVED
        assert grant.scopes_approved == ["query:event.*"]

    def test_create_first_party_grant_is_pending(self, grant_store):
        """First-party remote grants require approval (not auto-approved)."""
        grant, claim_secret = grant_store.create(
            client_id="first-party-agent",
            client_name="First Party Agent",
            scopes_requested=["query:event.detail"],
            reason="First party access",
            trust_tier=TrustTier.FIRST_PARTY_REMOTE,
        )

        # First-party remote has auto_approve=False in TIER_DEFAULTS
        assert grant.status == GrantStatus.PENDING
        assert grant.scopes_approved is None


class TestGrantApproval:
    def test_approve_grant(self, grant_store):
        """Approving a grant sets scopes and expiry."""
        grant, _ = grant_store.create(
            client_id="test-client",
            client_name="Test App",
            scopes_requested=["query:event.*", "observe:event"],
            reason="Testing",
            trust_tier=TrustTier.THIRD_PARTY,
        )

        approved = grant_store.approve(grant.grant_id, lifetime_hours=2)

        assert approved.status == GrantStatus.APPROVED
        assert approved.scopes_approved == ["query:event.*", "observe:event"]
        assert approved.expires_at is not None

    def test_approve_with_reduced_scopes(self, grant_store):
        """Can approve with fewer scopes than requested."""
        grant, _ = grant_store.create(
            client_id="test-client",
            client_name="Test App",
            scopes_requested=["query:event.*", "observe:event"],
            reason="Testing",
            trust_tier=TrustTier.THIRD_PARTY,
        )

        approved = grant_store.approve(
            grant.grant_id,
            scopes=["query:event.summary"],  # Reduced
        )

        assert approved.scopes_approved == ["query:event.summary"]

    def test_deny_grant(self, grant_store):
        """Denying a grant records reason."""
        grant, _ = grant_store.create(
            client_id="test-client",
            client_name="Test App",
            scopes_requested=["query:event.*"],
            reason="Testing",
            trust_tier=TrustTier.THIRD_PARTY,
        )

        denied = grant_store.deny(grant.grant_id, reason="Not authorized")

        assert denied.status == GrantStatus.DENIED
        assert denied.denial_reason == "Not authorized"

    def test_revoke_approved_grant(self, grant_store):
        """Can revoke an approved grant."""
        grant, _ = grant_store.create(
            client_id="test-client",
            client_name="Test App",
            scopes_requested=["query:event.*"],
            reason="Testing",
            trust_tier=TrustTier.THIRD_PARTY,
        )
        grant_store.approve(grant.grant_id)

        revoked = grant_store.revoke(grant.grant_id)

        assert revoked.status == GrantStatus.REVOKED


class TestTokenIssuance:
    def test_issue_token_requires_claim_secret(self, grant_store):
        """Third-party grants require claim secret to issue token."""
        grant, claim_secret = grant_store.create(
            client_id="test-client",
            client_name="Test App",
            scopes_requested=["query:event.summary"],
            reason="Testing",
            trust_tier=TrustTier.THIRD_PARTY,
        )
        grant_store.approve(grant.grant_id)

        # Without secret - should fail
        result = grant_store.issue_token(grant.grant_id)
        assert result is None

        # With wrong secret - should fail
        result = grant_store.issue_token(grant.grant_id, "wrong-secret")
        assert result is None

        # With correct secret - should succeed
        result = grant_store.issue_token(grant.grant_id, claim_secret)
        assert result is not None
        token_string, updated_grant = result
        assert token_string.startswith("pcp_")

    def test_cannot_issue_token_for_denied_grant(self, grant_store):
        """Cannot issue token for denied grant."""
        grant, claim_secret = grant_store.create(
            client_id="test-client",
            client_name="Test App",
            scopes_requested=["query:event.summary"],
            reason="Testing",
            trust_tier=TrustTier.THIRD_PARTY,
        )
        grant_store.deny(grant.grant_id)

        result = grant_store.issue_token(grant.grant_id, claim_secret)
        assert result is None

    def test_cannot_issue_token_for_pending_grant(self, grant_store):
        """Cannot issue token for pending grant."""
        grant, claim_secret = grant_store.create(
            client_id="test-client",
            client_name="Test App",
            scopes_requested=["query:event.summary"],
            reason="Testing",
            trust_tier=TrustTier.THIRD_PARTY,
        )
        # Don't approve - still pending

        result = grant_store.issue_token(grant.grant_id, claim_secret)
        assert result is None


class TestGrantPersistence:
    def test_grants_persist_across_restarts(self, temp_data_dir):
        """Grants survive store recreation."""
        # Create and approve grant
        store1 = GrantStore(data_dir=temp_data_dir)
        grant, claim_secret = store1.create(
            client_id="test-client",
            client_name="Test App",
            scopes_requested=["query:event.summary"],
            reason="Testing",
            trust_tier=TrustTier.THIRD_PARTY,
        )
        store1.approve(grant.grant_id)

        # Recreate store (simulates restart)
        store2 = GrantStore(data_dir=temp_data_dir)

        # Grant should still exist and be approved
        loaded = store2.get(grant.grant_id)
        assert loaded is not None
        assert loaded.status == GrantStatus.APPROVED

    def test_list_grants_by_status(self, grant_store):
        """Can list grants filtered by status."""
        # Create multiple grants
        grant1, _ = grant_store.create(
            client_id="client1",
            client_name="Client 1",
            scopes_requested=["query:event.*"],
            reason="Testing",
            trust_tier=TrustTier.THIRD_PARTY,
        )
        grant2, _ = grant_store.create(
            client_id="client2",
            client_name="Client 2",
            scopes_requested=["query:event.*"],
            reason="Testing",
            trust_tier=TrustTier.THIRD_PARTY,
        )
        grant_store.approve(grant1.grant_id)

        # List pending
        pending = grant_store.list_grants(status=GrantStatus.PENDING)
        assert len(pending) == 1
        assert pending[0].grant_id == grant2.grant_id

        # List approved
        approved = grant_store.list_grants(status=GrantStatus.APPROVED)
        assert len(approved) == 1
        assert approved[0].grant_id == grant1.grant_id
