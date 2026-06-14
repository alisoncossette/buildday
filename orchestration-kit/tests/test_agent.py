"""Stead — Tier 4: an agent acts on Ruby's behalf, ONLY inside a live parameterized grant.
SUBJECT to PRD.md. Starts RED."""
from consent_agent import ConsentEngine

MOM, AGENT = "mom", "agent:ruby"


def granted(cap=30):
    e = ConsentEngine(owner=MOM)
    e.grant(MOM, AGENT, "order:food", vendor="Tony's Pizza", cap=cap, payment="card-on-file")
    return e


def test_orders_within_grant():
    assert granted().act(AGENT, "order:food", vendor="Tony's Pizza", amount=28)["status"] == "done"


def test_over_cap_halts():
    # agent pauses and asks; it does NOT act
    assert granted(30).act(AGENT, "order:food", vendor="Tony's Pizza", amount=34)["status"] == "halted"


def test_raise_cap_on_the_fly_then_proceeds():
    e = granted(30)
    assert e.act(AGENT, "order:food", vendor="Tony's Pizza", amount=34)["status"] == "halted"
    e.update_grant(MOM, AGENT, "order:food", cap=40)  # Mom raises the limit live
    assert e.act(AGENT, "order:food", vendor="Tony's Pizza", amount=34)["status"] == "done"


def test_wrong_vendor_halts():
    assert granted().act(AGENT, "order:food", vendor="Sushi Palace", amount=10)["status"] == "halted"


def test_revoke_halts_instantly():
    e = granted()
    e.revoke(MOM, AGENT, "order:food")
    assert e.act(AGENT, "order:food", vendor="Tony's Pizza", amount=20)["status"] == "halted"


def test_no_action_without_a_logged_grant():
    e = ConsentEngine(owner=MOM)  # no grant issued
    assert e.act(AGENT, "order:food", vendor="Tony's Pizza", amount=10)["status"] == "halted"
    assert all(ev.get("decision") != "done" for ev in e.audit)
