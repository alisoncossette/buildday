"""Demo: Ruby runs her OWN scheduling, inside Mom's permission envelope. Offline, no keys, no hardware.

    python agent/schedule_demo.py

Shows the whole loop: Mom sets the envelope -> Ruby books inside it herself -> a request outside it
PARKS for Mom -> Mom widens the envelope live -> Ruby proceeds. Every step audited via ConsentEngine
(swap in the live Bolo MCP by constructing ConsentEngine(bolo=<@bolospot/mcp client>)).
"""
import os
import sys

_HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(_HERE, "..", "orchestration-kit"))

from consent_agent import ConsentEngine
from consent_agent.schedule import Scheduler, Caregiver, SCHEDULE_SCOPE

OWNER, RUBY = "mom", "agent:ruby"

ROSTER = [
    Caregiver("jane", "Jane", available={"Mon-AM", "Tue-PM", "Wed-AM"}, activities={"walk", "art"}),
    Caregiver("alex", "Alex", available={"Mon-PM", "Wed-PM", "Fri-AM"}, activities={"music", "walk"}),
    Caregiver("sam",  "Sam",  available={"Sat-AM", "Sun-AM"},           activities={"swim", "walk"}),
]


def show(title, res):
    print(f"\n>>> Ruby: {title}\n    -> {res['status'].upper()}: "
          + (res.get("reason") or f"{res.get('activity')} with {res.get('caregiver')} @ {res.get('slot')}")
          + (f"  | options: {res['alternatives']}" if res.get("alternatives") else ""))


consent = ConsentEngine(owner=OWNER)  # in-memory (offline). Use ConsentEngine(bolo=client) for real Bolo.
sched = Scheduler(consent, requester=RUBY, roster=ROSTER)

# --- Mom sets Ruby's scheduling ENVELOPE: what Ruby may book on her own ---
consent.grant(OWNER, RUBY, SCHEDULE_SCOPE,
              activities=["walk", "art", "music"],
              caregivers=["jane", "alex"],
              slots=["Mon-AM", "Mon-PM", "Tue-PM", "Wed-AM", "Wed-PM", "Fri-AM"])
print("MOM set Ruby's envelope: weekday daytime, {walk, art, music}, with Jane or Alex.")

# 1) Inside the envelope AND a caregiver is free + open to it -> Ruby books it HERSELF. No Mom.
show("'I want to do art with Jane, Wednesday morning'", sched.propose("art", "jane", "Wed-AM"))

# 2) Inside the envelope, but the caregiver can't -> unavailable, with alternatives (not a dead end).
show("'music with Jane, Monday morning'  (Jane doesn't do music)", sched.propose("music", "jane", "Mon-AM"))

# 3) OUTSIDE the envelope (swim, Sam, weekend) -> Ruby can't do it alone; it PARKS a request for Mom.
res = sched.propose("swim", "sam", "Sat-AM")
show("'I want to go swimming with Sam on Saturday'  (outside the envelope)", res)

# Mom sees Ruby's request and adjusts the envelope LIVE (raise-the-cap, but for scheduling).
if res["status"] == "needs_consent":
    consent.update_grant(OWNER, RUBY, SCHEDULE_SCOPE,
                         activities=["walk", "art", "music", "swim"],
                         caregivers=["jane", "alex", "sam"],
                         slots=["Mon-AM", "Mon-PM", "Tue-PM", "Wed-AM", "Wed-PM", "Fri-AM", "Sat-AM", "Sun-AM"])
    print("\nMOM widened Ruby's envelope live: + swim, + Sam, + weekends.")
    show("'...so, swimming with Sam on Saturday?'", sched.propose("swim", "sam", "Sat-AM"))

print("\n--- Ruby changes her mind (her right; in-envelope changes need no Mom) ---")
show("'actually... move swimming to Sunday'", sched.reschedule("swim", "sam", "Sat-AM", new_slot="Sun-AM"))

print("\n--- AUDIT (the consent receipts: every grant / request / action) ---")
for e in consent.audit:
    print("   ", {k: v for k, v in e.items() if v is not None})

print("\n--- RUBY'S BOOKINGS (made by her, inside what Mom allowed) ---")
for b in sched.bookings:
    print("   ", b)
