"""Stead — care-companion agent (Claude Agent SDK: Claude API + tool runner).

A voice agent that acts on Ruby's behalf ONLY inside scoped, revocable, owner-held consent:
  - speaks through MARS (Vocalizer) so the room hears the negotiation + consent moments,
  - orders food within a Bolo-backed grant (ConsentEngine) and HALTS on overstep,
  - gives the PCA an instant shift handoff (CareLog).

Runs OFFLINE by default (in-memory consent backend) so it's demoable on a hotspot; inject a live
Bolo MCP client for real grants (see ConsentEngine(bolo=...)). Model: claude-opus-4-8, adaptive thinking.

Prereqs:  pip install -r agent/requirements.txt ; export ANTHROPIC_API_KEY=...
          (and the consent core in ../orchestration-kit must be implemented — the build-verify
           spiral builds it to green.)
Run:      python agent/stead_agent.py "Ruby wants a pizza from Tony's, about $28"
"""
import os
import sys

# Make the consent core (built by the spiral) importable, AND the app data store (the shared brain).
_HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(_HERE, "..", "orchestration-kit"))
sys.path.insert(0, os.path.join(_HERE, "..", "app"))

import anthropic
from anthropic import beta_tool

from consent_agent import ConsentEngine, StoreBackend
from consent_agent.care import CareLog
from consent_agent.voice import Vocalizer
from tools import phone

MODEL = "claude-opus-4-8"
OWNER, AGENT_ID = "mom", "agent:ruby"

# THE SHARED BRAIN: the agent and the app construct ConsentEngine over the SAME StoreBackend
# (app/store.py). A cap change or revoke in the app is seen here, in this separate process, on the
# next consent check. The in-memory backend stays the default for offline pytest / grade.py.
try:
    consent = ConsentEngine(owner=OWNER, backend=StoreBackend())
except Exception:
    # If the store is unavailable for any reason, degrade to a deterministic in-memory backend.
    consent = ConsentEngine(owner=OWNER, bolo=None)
care = CareLog()
mars = Vocalizer(sink="mars")

# Idempotent baseline grant: this vendor, this cap, this payment (StoreBackend merges, safe to repeat).
consent.grant(OWNER, AGENT_ID, "order:food", vendor="Tony's Pizza", cap=30, payment="card-on-file")


@beta_tool
def speak_through_mars(text: str) -> str:
    """Speak `text` aloud through Ruby's MARS robot so the room hears it.

    Args:
        text: What to say, in plain language.
    """
    u = mars.say(text)
    return f"spoken via {u['sink']}: {u['text']}"


@beta_tool
def order_food(vendor: str, amount: float, vendor_phone: str = "", items: str = "a pizza") -> str:
    """Order food on Ruby's behalf by PLACING A REAL PHONE CALL to the vendor and ordering by voice.
    Allowed ONLY within Mom's live grant (right vendor, within cap); otherwise HALTS without dialing.

    Args:
        vendor: The restaurant to call.
        amount: Total order budget in US dollars.
        vendor_phone: Vendor phone in E.164 (e.g. +14155550123). Falls back to STEAD_TARGET_PHONE.
        items: What to order, e.g. 'a large pepperoni pizza'.
    """
    decision = consent.act(AGENT_ID, "order:food", vendor=vendor, amount=amount)
    if decision["status"] != "done":
        reason = decision.get("reason", "outside the live grant")
        return (f"HALTED before dialing: not authorized to spend ${amount:.2f} at {vendor} ({reason}). "
                f"Consent is required — ask Mom to raise the cap or approve. Do NOT claim it was ordered.")

    number = vendor_phone or os.environ.get("STEAD_TARGET_PHONE", "")
    if not number:
        return (f"AUTHORIZED ${amount:.2f} at {vendor}, but I have no number to call — "
                f"provide vendor_phone or set STEAD_TARGET_PHONE.")

    system = (f"You are placing a food order by phone on behalf of Ruby. Order: {items}. Stay within "
              f"${amount:.0f}. Be warm, clear, and brief: greet, place the order, confirm the total and "
              f"pickup ETA, thank them, end the call. If the total would exceed ${amount:.0f}, say you "
              f"need to check and do not commit.")
    opening = "Hi! I'd like to place a pickup order, please."
    mars.say(f"Calling {vendor} to order {items} for Ruby, up to ${amount:.0f}.")
    call = phone.place_call(number, system, opening)
    if call.get("mock"):
        return (f"AUTHORIZED ${amount:.2f} at {vendor}. [MOCK — set VAPI_API_KEY + VAPI_PHONE_NUMBER_ID "
                f"to dial for real] Would call {call['would_call']} and open with: \"{opening}\"")
    info = phone.poll_until_ended(call["call_id"])
    return (f"CALLED {vendor} at {number} (call {info.get('status')}). "
            f"{info.get('summary') or 'Order placed by voice within the granted budget.'}")


@beta_tool
def order_through_mars(vendor: str, amount: float, items: str = "a pizza") -> str:
    """Order food on Ruby's behalf and SPEAK the moment aloud through MARS so the room hears it.
    Uses the SAME shared consent path as order_food: within Mom's live grant it proceeds; over the
    cap it parks a consent request (Mom must approve to raise the cap); otherwise it HALTS. Never
    pretends an order went through.

    Args:
        vendor: The restaurant to order from.
        amount: Total order budget in US dollars.
        items: What to order, e.g. 'a large pepperoni pizza'.
    """
    decision = consent.act(AGENT_ID, "order:food", vendor=vendor, amount=amount)
    if decision["status"] == "done":
        mars.say(f"Ordering {items} from {vendor} for Ruby, ${amount:.0f}, on the card Mom shared.")
        return (f"AUTHORIZED ${amount:.2f} at {vendor} for {items} (spoken through MARS). "
                f"Proceeding within Mom's live grant.")
    reason = decision.get("reason", "outside the live grant")
    if reason == "over cap":
        cap = (consent._backend.get_grant(AGENT_ID, "order:food") or {}).get("cap")
        consent.request_access(AGENT_ID, "order:food", vendor=vendor, amount=amount, cap=amount)
        mars.say(f"That's ${amount:.0f} at {vendor}, over the ${cap or 0:.0f} limit. "
                 f"I'm asking Mom to approve raising the cap before I order.")
        return (f"NEEDS CONSENT: ${amount:.2f} exceeds the ${cap or 0:.0f} cap. Parked a request for "
                f"Mom to approve (spoken through MARS). Do NOT claim it was ordered.")
    mars.say(f"I can't order from {vendor} right now — {reason}. Consent is needed.")
    return (f"HALTED: not authorized to spend ${amount:.2f} at {vendor} ({reason}). "
            f"Spoke the halt through MARS. Ask Mom to adjust consent.")


@beta_tool
def care_handoff(day: str) -> str:
    """Get the 20-second shift-handoff summary of Ruby's day for an arriving caregiver.

    Args:
        day: ISO date, e.g. 2026-06-13.
    """
    return care.handoff_summary(day)


SYSTEM = (
    "You are Stead, a warm care companion acting on behalf of Ruby, a young woman with cerebral palsy. "
    "You may act for her ONLY inside the scoped, revocable consent her family grants. To order on her "
    "behalf use order_food; if it HALTS, say plainly that consent is needed and never pretend the order "
    "went through. Speak the important moments aloud through MARS with speak_through_mars — the order, "
    "and any consent halt — so the room hears them. Be concise and kind."
)


def run(user_text: str) -> None:
    client = anthropic.Anthropic()
    runner = client.beta.messages.tool_runner(
        model=MODEL,
        max_tokens=4096,
        thinking={"type": "adaptive"},
        system=SYSTEM,
        tools=[speak_through_mars, order_food, order_through_mars, care_handoff],
        messages=[{"role": "user", "content": user_text}],
    )
    for message in runner:
        for block in message.content:
            if block.type == "text":
                print(block.text)


if __name__ == "__main__":
    run(" ".join(sys.argv[1:]) or "Ruby wants a pizza from Tony's, about $28.")
