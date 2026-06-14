"""Stead — THE SHARED BRAIN: grants + pending consent-requests persist in app/store.py so the app
and the agent (separate processes) see the SAME live consent state. A cap change / revoke in one is
visible to the other on its next check. Also covers the 'over cap -> needs_consent' parking flow.

These tests drive a FRESH SQLite store (an isolated temp DB) so they never touch Postgres and stay
deterministic offline. The default InMemoryBackend remains the default for the rest of the suite.
"""
import importlib
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "orchestration-kit"))
sys.path.insert(0, os.path.join(ROOT, "app"))

MOM, AGENT = "mom", "agent:ruby"


@pytest.fixture()
def store(tmp_path, monkeypatch):
    """A store module bound to a throwaway SQLite file (no DATABASE_URL -> SQLite path)."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    import store as _store
    importlib.reload(_store)
    _store.DB_PATH = tmp_path / "test_stead.db"
    _store._PG = False
    _store.init_db()
    return _store


def _engine(store):
    from consent_agent import ConsentEngine, StoreBackend
    return ConsentEngine(owner=MOM, backend=StoreBackend(store))


def test_two_engines_share_grants_via_store(store):
    app, agent = _engine(store), _engine(store)  # two "processes" over one persistent brain
    app.grant(MOM, AGENT, "order:food", vendor="Tony's Pizza", cap=30)
    # The agent (a separate engine) sees the grant the app minted.
    assert agent.act(AGENT, "order:food", vendor="Tony's Pizza", amount=25)["status"] == "done"


def test_cap_change_in_app_seen_by_agent(store):
    app, agent = _engine(store), _engine(store)
    app.grant(MOM, AGENT, "order:food", vendor="Tony's Pizza", cap=30)
    assert agent.act(AGENT, "order:food", vendor="Tony's Pizza", amount=50)["status"] == "halted"
    app.update_grant(MOM, AGENT, "order:food", cap=60)
    assert agent.act(AGENT, "order:food", vendor="Tony's Pizza", amount=50)["status"] == "done"


def test_revoke_in_app_halts_agent(store):
    app, agent = _engine(store), _engine(store)
    app.grant(MOM, AGENT, "order:food", vendor="Tony's Pizza", cap=30)
    assert agent.act(AGENT, "order:food", vendor="Tony's Pizza", amount=20)["status"] == "done"
    app.revoke(MOM, AGENT, "order:food")
    assert agent.act(AGENT, "order:food", vendor="Tony's Pizza", amount=20)["status"] == "halted"


def test_over_cap_parks_request_then_approval_unblocks(store):
    app, agent = _engine(store), _engine(store)
    app.grant(MOM, AGENT, "order:food", vendor="Tony's Pizza", cap=30)
    # Over cap halts the action...
    assert agent.act(AGENT, "order:food", vendor="Tony's Pizza", amount=50)["status"] == "halted"
    # ...so the agent parks a consent request, which the APP sees in the shared queue.
    req = agent.request_access(AGENT, "order:food", vendor="Tony's Pizza", amount=50, cap=50)
    pending = [r for r in app.requests if r["status"] == "pending"]
    assert any(r["id"] == req["id"] for r in pending)
    # Owner approves in the app; the agent can now act.
    app.approve_request(MOM, req["id"])
    assert agent.act(AGENT, "order:food", vendor="Tony's Pizza", amount=50)["status"] == "done"


def test_deny_in_app_keeps_agent_blocked(store):
    app, agent = _engine(store), _engine(store)
    app.grant(MOM, AGENT, "order:food", vendor="Tony's Pizza", cap=30)
    req = agent.request_access(AGENT, "order:food", vendor="Tony's Pizza", amount=50, cap=50)
    app.deny_request(MOM, req["id"])
    assert agent.act(AGENT, "order:food", vendor="Tony's Pizza", amount=50)["status"] == "halted"
