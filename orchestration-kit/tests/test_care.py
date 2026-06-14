"""Stead — Tier 2: the care companion. Instant PCA handoff + health tracking, gated by RBAC.
SUBJECT to PRD.md. Starts RED."""
from consent_agent import ConsentEngine
from consent_agent.care import CareLog

MOM, PCA = "mom", "jane:pca"


def seeded():
    c = CareLog()
    c.record("2026-06-11", mood="ok", meds="on time", food_intake=0.9)
    c.record("2026-06-12", mood="low", meds="on time", food_intake=0.6)
    c.record("2026-06-13", mood="low", meds="on time", food_intake=0.4)
    return c


def test_handoff_summary_is_short_and_grounded():
    s = seeded().handoff_summary("2026-06-13")
    assert isinstance(s, str) and 0 < len(s) <= 280
    assert "meds" in s.lower()  # grounded in what was actually recorded


def test_tracking_trend_declines_over_time():
    # appetite falling 0.9 -> 0.6 -> 0.4 is what the doctor needs to see
    assert seeded().trends("food_intake", window=3)["direction"] == "down"


def test_pca_can_read_todays_handoff_but_not_full_history():
    e = ConsentEngine(owner=MOM)
    e.grant(MOM, PCA, "care:read")
    assert e.can_view(PCA, "care:today") is True
    assert e.can_view(PCA, "care:history") is False  # shift-scoped, not the whole record
