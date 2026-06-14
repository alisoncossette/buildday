"""Stead — scheduling tools for Ruby's agent. Ruby schedules her OWN day BY VOICE, inside Mom's
permission envelope. Thin @beta_tool wrappers over Scheduler + ConsentEngine (the shared brain, so a
cap/envelope change in the app is seen here on the next check). SUBJECT to PRD.md.

Wire into the agent by importing these tools into the tool_runner's `tools=[...]` list.
"""
import json
import os
import sys

_HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(_HERE, "..", "..", "orchestration-kit"))
sys.path.insert(0, os.path.join(_HERE, "..", "..", "app"))

from anthropic import beta_tool

from consent_agent import ConsentEngine, StoreBackend
from consent_agent.schedule import Scheduler, Caregiver

OWNER, RUBY = "mom", "agent:ruby"

# Caregiver roster: agent/tools/caregivers.json if present, else a sensible default.
_DEFAULT_ROSTER = [
    {"id": "jane", "name": "Jane", "available": ["Mon-AM", "Tue-PM", "Wed-AM"], "activities": ["walk", "art"]},
    {"id": "alex", "name": "Alex", "available": ["Mon-PM", "Wed-PM", "Fri-AM"], "activities": ["music", "walk"]},
    {"id": "sam",  "name": "Sam",  "available": ["Sat-AM", "Sun-AM"],           "activities": ["swim", "walk"]},
]


def _load_roster():
    path = os.path.join(_HERE, "caregivers.json")
    rows = _DEFAULT_ROSTER
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                rows = json.load(f)
        except Exception:
            rows = _DEFAULT_ROSTER
    return [Caregiver(r["id"], r["name"], set(r["available"]), set(r["activities"])) for r in rows]


# Shared brain: same StoreBackend the app + order agent use, so Mom's envelope changes are seen live.
try:
    _consent = ConsentEngine(owner=OWNER, backend=StoreBackend())
except Exception:
    _consent = ConsentEngine(owner=OWNER)  # deterministic offline fallback
_sched = Scheduler(_consent, requester=RUBY, roster=_load_roster())


def _fmt(res) -> str:
    s = res["status"]
    if s == "booked":
        return f"Done — {res['activity']} with {res['caregiver']} at {res['slot']} is booked."
    if s == "cancelled":
        return f"Okay, cancelled {res['activity']} with {res['caregiver']} at {res['slot']}."
    if s == "needs_consent":
        return (f"I can't set that up on my own — {res['reason']}. I've asked Mom to approve it "
                f"(request {res.get('request_id')}). I won't pretend it's booked.")
    if s == "unavailable":
        alt = res.get("alternatives") or []
        opts = "; ".join(f"{a['name']} at {a['slot']}" for a in alt) if alt else "no one else is open to that then"
        return f"That won't work — {res['reason']}. Other options: {opts}."
    if s == "not_found":
        return "I don't see that on Ruby's schedule to change."
    return f"{s}: {res}"


@beta_tool
def schedule_activity(activity: str, caregiver: str, slot: str) -> str:
    """Schedule an activity for Ruby with a caregiver at a time slot, on Ruby's behalf. Books it ONLY
    if it's inside the envelope Mom set AND the caregiver is free and open to it; otherwise it asks Mom
    and never pretends it booked.

    Args:
        activity: e.g. 'art', 'walk', 'music', 'swim'.
        caregiver: caregiver id, e.g. 'jane'.
        slot: time-slot tag, e.g. 'Wed-AM'.
    """
    return _fmt(_sched.propose(activity, caregiver, slot))


@beta_tool
def cancel_activity(activity: str, caregiver: str, slot: str) -> str:
    """Cancel one of Ruby's booked activities (she changed her mind). Always hers to do — no Mom needed.

    Args:
        activity: the activity to cancel.
        caregiver: caregiver id.
        slot: the booked slot.
    """
    return _fmt(_sched.cancel(activity, caregiver, slot))


@beta_tool
def reschedule_activity(activity: str, caregiver: str, slot: str,
                        new_slot: str = "", new_caregiver: str = "") -> str:
    """Move one of Ruby's activities to a new time and/or caregiver (she changed her mind). Re-checks
    Mom's envelope + availability for the new request.

    Args:
        activity: the activity to move.
        caregiver: current caregiver id.
        slot: current slot.
        new_slot: the new slot (optional).
        new_caregiver: the new caregiver id (optional).
    """
    return _fmt(_sched.reschedule(activity, caregiver, slot,
                                  new_caregiver=new_caregiver or None, new_slot=new_slot or None))


@beta_tool
def caregiver_options(activity: str, slot: str) -> str:
    """List caregivers who are free at `slot` and open to `activity`, so Ruby has real choices.

    Args:
        activity: e.g. 'walk'.
        slot: e.g. 'Mon-AM'.
    """
    alts = _sched._alternatives(activity, slot)
    if not alts:
        return f"No caregiver is free for {activity} at {slot}."
    return "Available: " + "; ".join(f"{a['name']} ({a['caregiver']})" for a in alts)


SCHEDULING_TOOLS = [schedule_activity, cancel_activity, reschedule_activity, caregiver_options]
