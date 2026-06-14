"""Stead — Ruby schedules her own day, inside Mom's permission envelope. SUBJECT to PRD.md.

THE OUTCOME: Ruby runs her own life. She asks her agent to schedule an activity; the agent books it
ITSELF when the request is inside the envelope Mom set (allowed activities, allowed caregivers,
allowed time slots) AND a caregiver is actually free and open to that activity. Anything outside the
envelope is NOT done silently — it PARKS a consent request for Mom (Ruby asks; Mom decides or widens
the envelope live), exactly like the order->cap flow. Every decision is audited through the SAME
ConsentEngine (Bolo-backed when a live @bolospot/mcp client is injected).

Authority lives in ConsentEngine; this module never grants. It owns the caregiver roster (who is
free when, and who is open to what) + the matching, reads the live envelope grant, and either books
or parks a request for Mom. Offline-first (no keys) so it's demoable on a hotspot / a boat.
"""

from dataclasses import dataclass

# The scope Mom grants Ruby's agent: "you may schedule activities, inside these parameters."
SCHEDULE_SCOPE = "schedule:activity"


@dataclass
class Caregiver:
    """A caregiver: when they're free (slot tags) and which activities they're open to."""
    id: str
    name: str
    available: set      # slot tags they are free, e.g. {"Mon-AM", "Tue-PM"}
    activities: set     # activities they will do, e.g. {"walk", "art"}

    def can(self, activity, slot) -> bool:
        return slot in self.available and activity in self.activities


class Scheduler:
    """Books Ruby's activities inside Mom's envelope. Wraps a ConsentEngine for authority + audit."""

    def __init__(self, consent, requester="agent:ruby", roster=None):
        self.consent = consent
        self.requester = requester
        self.roster = {c.id: c for c in (roster or [])}
        self.bookings = []  # confirmed bookings

    def _envelope(self):
        """The live scheduling grant Mom set for Ruby's agent (its params), or None.
        Read via the backend the same way the order agent reads its grant — no core change."""
        return self.consent._backend.get_grant(self.requester, SCHEDULE_SCOPE)

    @staticmethod
    def _violations(env, activity, caregiver, slot):
        """Which envelope constraints (if any) this request breaks. Empty list == inside the envelope."""
        bad = []
        acts = env.get("activities")
        if acts is not None and activity not in acts:
            bad.append(f"'{activity}' isn't one of Ruby's allowed activities")
        cgs = env.get("caregivers")
        if cgs is not None and "*" not in cgs and caregiver not in cgs:
            bad.append(f"'{caregiver}' isn't one of Ruby's allowed caregivers")
        slots = env.get("slots")
        if slots is not None and slot not in slots:
            bad.append(f"'{slot}' is outside Ruby's allowed times")
        return bad

    def _alternatives(self, activity, slot):
        """Other caregivers who could do `activity` at `slot` — so Ruby gets options, not a dead end."""
        return [
            {"caregiver": cg.id, "name": cg.name, "slot": slot}
            for cg in self.roster.values()
            if cg.can(activity, slot)
        ]

    def propose(self, activity, caregiver, slot) -> dict:
        """Ruby asks to schedule `activity` with `caregiver` at `slot`.

        Returns one of:
          {'status': 'booked', ...}                  — inside the envelope AND feasible; Ruby did it herself
          {'status': 'needs_consent', 'request_id'}  — outside the envelope; parked for Mom (Ruby asked)
          {'status': 'unavailable', 'alternatives'}  — inside the envelope but caregiver can't; here are options
        """
        env = self._envelope()
        if env is None:
            req = self.consent.request_access(
                self.requester, SCHEDULE_SCOPE, activity=activity, caregiver=caregiver, slot=slot)
            return {"status": "needs_consent", "reason": "Mom hasn't set a scheduling envelope yet",
                    "request_id": req["id"]}

        # 1) AUTHORITY — is this the kind of thing Mom let Ruby arrange on her own?
        violations = self._violations(env, activity, caregiver, slot)
        if violations:
            req = self.consent.request_access(
                self.requester, SCHEDULE_SCOPE, activity=activity, caregiver=caregiver, slot=slot)
            return {"status": "needs_consent", "reason": "; ".join(violations),
                    "request_id": req["id"]}

        # 2) FEASIBILITY — is this caregiver actually free, and open to this activity?
        cg = self.roster.get(caregiver)
        if cg is None:
            return {"status": "unavailable", "reason": f"unknown caregiver '{caregiver}'",
                    "alternatives": self._alternatives(activity, slot)}
        if not cg.can(activity, slot):
            why = []
            if slot not in cg.available:
                why.append(f"{cg.name} isn't free {slot}")
            if activity not in cg.activities:
                why.append(f"{cg.name} doesn't do {activity}")
            return {"status": "unavailable", "reason": "; ".join(why),
                    "alternatives": self._alternatives(activity, slot)}

        # 3) Inside the envelope AND feasible -> Ruby books it herself. Audited via ConsentEngine.act.
        self.consent.act(self.requester, SCHEDULE_SCOPE,
                         activity=activity, caregiver=caregiver, slot=slot)
        booking = {"activity": activity, "caregiver": caregiver, "slot": slot}
        self.bookings.append(booking)
        return {"status": "booked", **booking}

    def cancel(self, activity, caregiver, slot) -> dict:
        """Ruby changes her mind. Cancelling her OWN booking is always hers to do — no Mom, no
        re-check (dropping a commitment never widens authority). Audited."""
        booking = {"activity": activity, "caregiver": caregiver, "slot": slot}
        if booking in self.bookings:
            self.bookings.remove(booking)
            self.consent.act(self.requester, SCHEDULE_SCOPE, change="cancel", **booking)
            return {"status": "cancelled", **booking}
        return {"status": "not_found", **booking}

    def reschedule(self, activity, caregiver, slot, new_caregiver=None, new_slot=None) -> dict:
        """Ruby changes her mind about WHEN/WHO. Drop the old booking (always hers), then propose the
        new one — which re-runs the FULL envelope + feasibility check (a change can't sneak past Mom's
        envelope). This is principal-intent-change (authorized), NOT agent drift (which the monitor halts)."""
        self.cancel(activity, caregiver, slot)
        return self.propose(activity, new_caregiver or caregiver, new_slot or slot)
