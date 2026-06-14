"""Stead — Tier 6: request -> approve -> act, with ATTENUATING delegation.

Ruby (or her agent) asks for a BRAND-NEW capability she has no standing grant for (e.g. have MARS
take a selfie and post it publicly). That request is NOT self-granted: it parks as PENDING until the
OWNER (Mom) approves it, which mints a real scoped grant. Only then can the agent act. When the agent
spawns a sub-agent (the MARS selfie skill), the sub-grant must be a SUBSET of what it holds — capabilities
can only NARROW as they are delegated, never widen (least privilege). Every step is audited.

SUBJECT to PRD.md. Starts RED — the loop implements request_access / approve_request / deny_request /
delegate on ConsentEngine for real (no hardcoding, no weakened assertions).
"""
from consent_agent import ConsentEngine

MOM, RUBY, AGENT, MARS = "mom", "ruby", "agent:ruby", "agent:mars-selfie"


def engine():
    return ConsentEngine(owner=MOM)


def test_novel_request_is_not_self_granted():
    # Requesting a new capability parks as pending; it does NOT become actionable on its own.
    e = engine()
    req = e.request_access(AGENT, "post:photo", audience="public")
    assert req["status"] == "pending"
    assert e.act(AGENT, "post:photo", audience="public")["status"] == "halted"


def test_only_owner_may_approve():
    e = engine()
    req = e.request_access(AGENT, "post:photo", audience="public")
    try:
        e.approve_request(RUBY, req["id"])  # Ruby is not the owner
        assert False, "non-owner approval must raise PermissionError"
    except PermissionError:
        pass
    # still not actionable after a rejected (unauthorized) approval attempt
    assert e.act(AGENT, "post:photo", audience="public")["status"] == "halted"


def test_approve_mints_grant_then_agent_acts():
    e = engine()
    req = e.request_access(AGENT, "post:photo", audience="public")
    e.approve_request(MOM, req["id"])
    assert e.act(AGENT, "post:photo", audience="public")["status"] == "done"


def test_deny_blocks_action():
    e = engine()
    req = e.request_access(AGENT, "post:photo", audience="public")
    e.deny_request(MOM, req["id"])
    assert e.act(AGENT, "post:photo", audience="public")["status"] == "halted"


def test_owner_can_tighten_at_approval_time():
    # Mom approves but narrows the request: at most one post (cap=1). Over the cap halts.
    e = engine()
    req = e.request_access(AGENT, "post:photo", audience="public")
    e.approve_request(MOM, req["id"], cap=1)
    assert e.act(AGENT, "post:photo", audience="public", amount=1)["status"] == "done"
    assert e.act(AGENT, "post:photo", audience="public", amount=2)["status"] == "halted"


def test_delegation_attenuates_never_amplifies():
    # Agent holds an approved grant (cap=2) and spawns the MARS sub-agent.
    e = engine()
    req = e.request_access(AGENT, "post:photo", audience="public")
    e.approve_request(MOM, req["id"], cap=2)
    # Narrower slice (cap 1 <= 2) is allowed; the sub-agent can act within it.
    e.delegate(AGENT, MARS, "post:photo", cap=1)
    assert e.act(MARS, "post:photo", audience="public", amount=1)["status"] == "done"
    assert e.act(MARS, "post:photo", audience="public", amount=2)["status"] == "halted"
    # Trying to WIDEN beyond the parent's grant (cap 5 > 2) must be refused.
    try:
        e.delegate(AGENT, MARS, "post:photo", cap=5)
        assert False, "widening a delegated grant must be refused"
    except (PermissionError, ValueError):
        pass


def test_cannot_delegate_what_you_do_not_hold():
    # An actor with no grant for the scope cannot mint a sub-grant for it.
    e = engine()
    try:
        e.delegate(AGENT, MARS, "post:photo", cap=1)
        assert False, "delegating an unheld scope must be refused"
    except (PermissionError, ValueError):
        pass
    assert e.act(MARS, "post:photo", audience="public", amount=1)["status"] == "halted"


def test_request_and_decision_are_audited():
    e = engine()
    req = e.request_access(AGENT, "post:photo", audience="public")
    e.approve_request(MOM, req["id"])
    events = [ev.get("event") for ev in e.audit]
    assert "request" in events
    assert "approve" in events
