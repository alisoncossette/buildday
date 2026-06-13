#!/usr/bin/env python3
"""
PERMIT — an agent that negotiates only within its granted permissions,
and HALTS the instant consent is revoked.

v0: pure Python, zero deps, runs instantly. Mock "Bolo" gate stands in for
@bolospot/mcp so this runs with no key. Swap MockBolo -> real Bolo (and the
scripted turns -> Claude) once the spine is proven.

    python negotiate.py            # happy path: consent holds -> booking completes
    python negotiate.py --revoke   # consent revoked mid-negotiation -> agent HALTS

The whole demo is the difference between those two runs.
"""
import argparse
import sys
import time
from datetime import datetime, timezone

# Windows consoles default to cp1252 and choke on emoji/em-dash — force UTF-8.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# ----- audit trail (this is the trust story; later -> Langfuse) ---------------
AUDIT = []
def log(actor, event, detail=""):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    AUDIT.append((ts, actor, event, detail))
    print(f"  [{ts}] {actor:<14} {event}{(' - ' + detail) if detail else ''}")

# ----- the gate (MOCK Bolo) ---------------------------------------------------
# Real version: call @bolospot/mcp check_access / create_grant / revoke_grant.
# Design that beats the 'does revoke halt in-flight?' risk: the agent RE-CHECKS
# consent before EVERY step, so a revoke halts it at the very next step = mid-flight.
class MockBolo:
    def __init__(self):
        self.grants = {}  # (grantor, grantee, capability) -> True

    def create_grant(self, grantor, grantee, capability):
        self.grants[(grantor, grantee, capability)] = True
        log(grantor, "GRANT", f"{grantee} may {capability}")

    def revoke_grant(self, grantor, grantee, capability):
        self.grants.pop((grantor, grantee, capability), None)
        log(grantor, "REVOKE", f"{grantee} may NO LONGER {capability}")

    def check_access(self, grantor, grantee, capability):
        return self.grants.get((grantor, grantee, capability), False)


# ----- the negotiation (scripted in v0; -> Claude agents next) ----------------
PATIENT = "@patient"          # principal who owns the resource (their calendar/records)
CLINIC_AGENT = "@clinic-bot"  # the agent acting on the clinic's behalf
CAP = "book_on_calendar"      # the capability being negotiated/used

# A simple multi-step negotiation toward a confirmed booking.
TURNS = [
    (CLINIC_AGENT, "proposes follow-up: Tue 10:00"),
    (PATIENT,      "counters: Tue is bad, prefer Wed afternoon"),
    (CLINIC_AGENT, "offers: Wed 14:30"),
    (PATIENT,      "accepts Wed 14:30"),
    (CLINIC_AGENT, "books Wed 14:30 + shares visit summary"),  # the consequential action
]

def run(revoke_at=None):
    bolo = MockBolo()
    print("\n=== PERMIT — permission-bounded agent negotiation ===\n")

    # The patient grants the clinic's agent scoped authority to coordinate.
    bolo.create_grant(PATIENT, CLINIC_AGENT, CAP)
    print()

    for i, (actor, move) in enumerate(TURNS, start=1):
        # The human can pull consent at any moment.
        if revoke_at == i:
            print()
            bolo.revoke_grant(PATIENT, CLINIC_AGENT, CAP)
            print()

        # GATE: re-check consent before every step the agent takes on the patient's behalf.
        if actor == CLINIC_AGENT and not bolo.check_access(PATIENT, CLINIC_AGENT, CAP):
            log(CLINIC_AGENT, "HALT", "no consent — refusing to act, escalating to human")
            print("\n🛑 Agent HALTED mid-negotiation. It cannot act without a grant.\n")
            summary(completed=False)
            return

        log(actor, "turn", move)
        time.sleep(0.6)  # dramatic pacing for the live demo

    print("\n✅ Booking confirmed - every action was inside a live grant.\n")
    summary(completed=True)


def summary(completed):
    print("--- AUDIT (who did what, under whose consent) ---")
    for ts, actor, event, detail in AUDIT:
        print(f"  {ts}  {actor:<14} {event:<8} {detail}")
    print(f"\nOUTCOME: {'completed within consent' if completed else 'halted on revoke'}")
    grants_acted = sum(1 for a in AUDIT if a[2] == 'turn' and a[1] == CLINIC_AGENT)
    print(f"SCOREBOARD: agent actions taken = {grants_acted} · "
          f"actions without a logged grant = 0 · halted-on-revoke = {not completed}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--revoke", action="store_true",
                    help="patient revokes consent mid-negotiation (after step 2)")
    ap.add_argument("--revoke-at", type=int, default=None, help="revoke before this step #")
    args = ap.parse_args()
    at = args.revoke_at if args.revoke_at is not None else (3 if args.revoke else None)
    run(revoke_at=at)
