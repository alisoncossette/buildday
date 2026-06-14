"""Stead — observability shim is SAFE and INVISIBLE. SUBJECT to PRD.md.

Proves two things:
(a) with NO langfuse installed / NO keys set, importing and using the tracer is a silent no-op
    that never crashes and never touches the network;
(b) consent decisions still return the correct results while tracing is active.
"""
import os

from consent_agent import ConsentEngine
from consent_agent.trace import tracer, _NoopTracer, _langfuse_enabled

MOM, AGENT, PCA = "mom", "agent:ruby", "jane:pca"


def test_default_tracer_is_noop_with_zero_env():
    # No keys in the test env -> the module-level tracer must be the no-op.
    assert not (os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY"))
    assert _langfuse_enabled() is False
    assert isinstance(tracer, _NoopTracer)
    assert tracer.enabled is False


def test_noop_event_and_span_never_crash():
    # event() returns None and swallows arbitrary attrs.
    assert tracer.event("anything", actor=MOM, scope="order:food", cap=30, amount=99) is None
    # span() is a working context manager whose handle swallows update()/event().
    with tracer.span("blk", actor=AGENT, vendor="Tony's Pizza") as span:
        span.update(status="done", reason="within grant")
        span.event("inner", amount=10)
    # flush is safe to call too.
    assert tracer.flush() is None


def test_grant_and_act_correct_with_tracing_active():
    e = ConsentEngine(owner=MOM)
    e.grant(MOM, AGENT, "order:food", vendor="Tony's Pizza", cap=30, payment="card-on-file")

    assert e.act(AGENT, "order:food", vendor="Tony's Pizza", amount=28)["status"] == "done"
    assert e.act(AGENT, "order:food", vendor="Tony's Pizza", amount=34)["status"] == "halted"
    assert e.act(AGENT, "order:food", vendor="Sushi Palace", amount=10)["status"] == "halted"

    e.update_grant(MOM, AGENT, "order:food", cap=40)
    assert e.act(AGENT, "order:food", vendor="Tony's Pizza", amount=34)["status"] == "done"

    e.revoke(MOM, AGENT, "order:food")
    assert e.act(AGENT, "order:food", vendor="Tony's Pizza", amount=10)["status"] == "halted"

    # The in-memory audit is unaffected by tracing.
    assert any(ev["decision"] == "done" for ev in e.audit)
    assert any(ev["decision"] == "halted" for ev in e.audit)


def test_can_view_correct_with_tracing_active():
    e = ConsentEngine(owner=MOM)
    e.grant(MOM, PCA, "care:read")
    assert e.can_view(MOM, "care:trends") is True       # owner sees all
    assert e.can_view(PCA, "care:today") is True        # care:read gates today
    assert e.can_view(PCA, "care:trends") is False      # but not trends
    assert e.can_view("stranger", "care:today") is False
