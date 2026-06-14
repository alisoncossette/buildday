"""Stead — Tier 1: RBAC / consent access. SUBJECT to PRD.md. Starts RED.
Per-actor roles & scopes, owner-held grant/check/revoke, fully audited."""
from consent_agent import ConsentEngine

MOM, PCA, AGENT = "mom", "jane:pca", "agent:ruby"


def test_owner_has_full_access():
    e = ConsentEngine(owner=MOM)
    assert e.check(MOM, "care:read") is True
    assert e.check(MOM, "care:write") is True


def test_pca_is_scoped_to_care_read_only():
    e = ConsentEngine(owner=MOM)
    e.grant(MOM, PCA, "care:read")
    assert e.check(PCA, "care:read") is True
    assert e.check(PCA, "care:write") is False
    assert e.check(PCA, "order:food") is False


def test_only_owner_can_grant():
    e = ConsentEngine(owner=MOM)
    try:
        e.grant(PCA, PCA, "care:write")  # a non-owner must not be able to grant
        assert False, "non-owner must not grant"
    except PermissionError:
        pass


def test_revoke_takes_effect_immediately():
    e = ConsentEngine(owner=MOM)
    e.grant(MOM, PCA, "care:read")
    assert e.check(PCA, "care:read") is True
    e.revoke(MOM, PCA, "care:read")
    assert e.check(PCA, "care:read") is False


def test_audit_logs_grant_and_revoke():
    e = ConsentEngine(owner=MOM)
    e.grant(MOM, PCA, "care:read")
    e.revoke(MOM, PCA, "care:read")
    events = [ev.get("event") for ev in e.audit]
    assert "grant" in events and "revoke" in events
